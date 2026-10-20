"""Phase 3 planner loop tests. Offline: FakeLLM only, no network."""

from __future__ import annotations

import json

import pytest
from sparkplan.llm.phase3 import FakeLLM, LLMError, LLMRetryableError, LLMUsage
from sparkplan.planner.phase3 import MAX_LLM_CALLS, MAX_REPAIR_ROUNDS, Planner, PlanStatus
from tests.planner.phase3.conftest import unsupported_spec, valid_spec


def _bad_part(valid: dict) -> dict:
    bad = json.loads(json.dumps(valid))
    bad["parts"][1]["part_id"] = "part_that_does_not_exist"
    return bad


# ---------------------------------------------------------------------------
# Required behaviors
# ---------------------------------------------------------------------------


def test_success_first_try(catalog):
    llm = FakeLLM(script=[(valid_spec(), LLMUsage(120, 60, True))])
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.status is PlanStatus.OK
    assert result.spec is not None
    assert result.repair_rounds == 0
    assert result.llm_calls == 1
    assert len(llm.calls) == 1


def test_fails_once_then_repairs(catalog):
    llm = FakeLLM(script=[{"parts": "not-a-list"}, valid_spec()])
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.status is PlanStatus.OK
    assert result.repair_rounds == 1
    assert result.llm_calls == 2
    assert "SPEC_PARSE_ERROR" in llm.calls[1]["user"]


def test_truncated_response_triggers_repair(catalog):
    llm = FakeLLM(script=[(valid_spec(), None, "length"), (valid_spec(), None, "stop")])
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.status is PlanStatus.OK
    assert result.repair_rounds == 1
    assert result.llm_calls == 2
    assert "LLM_TRUNCATED" in llm.calls[1]["user"]
    assert result.attempts[0].kind == "truncated"


def test_always_truncated_is_failed_not_ok(catalog):
    llm = FakeLLM(script=[(valid_spec(), None, "length")] * 10)
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.status is PlanStatus.FAILED
    assert result.llm_calls == MAX_LLM_CALLS
    assert any(i.code == "LLM_TRUNCATED" for i in result.issues)


def test_fails_four_times_returns_failed(catalog):
    bad = _bad_part(valid_spec())
    llm = FakeLLM(script=[bad] * 10)
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.status is PlanStatus.FAILED
    assert result.repair_rounds == MAX_REPAIR_ROUNDS
    assert result.llm_calls == MAX_LLM_CALLS
    assert len(llm.calls) == MAX_LLM_CALLS
    assert result.report is not None  # final report is present
    assert any(i.code == "UNKNOWN_PART" for i in result.report.errors)


def test_drone_idea_is_unsupported(catalog):
    llm = FakeLLM(script=[unsupported_spec()])
    result = Planner(llm, catalog).plan("an autonomous drone")
    assert result.status is PlanStatus.UNSUPPORTED
    assert result.spec is None
    assert result.missing_capabilities
    assert "flight_control" in result.missing_capabilities
    assert result.closest_supported_alternative


def test_malformed_json_is_repaired(catalog):
    llm = FakeLLM(script=[{"status": "ok"}, valid_spec()])
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.status is PlanStatus.OK
    assert result.llm_calls == 2


def test_number_of_llm_calls_is_bounded(catalog):
    # Every trigger path must respect the 1 + 3 bound.
    for script in (
        [{"x": 1}] * 10,  # malformed JSON
        [_bad_part(valid_spec())] * 10,  # validator errors
        [LLMRetryableError("timeout")] * 10,  # transport failures
    ):
        llm = FakeLLM(script=script)
        result = Planner(llm, catalog).plan("a desk plant monitor")
        assert result.llm_calls <= MAX_LLM_CALLS
        assert len(llm.calls) <= MAX_LLM_CALLS


# ---------------------------------------------------------------------------
# Requirement 2: strict success invariant
# ---------------------------------------------------------------------------


def test_error_plus_warning_still_fails(catalog):
    # A build with one error and (potentially) warnings must not be "ok".
    llm = FakeLLM(script=[_bad_part(valid_spec())] * 10)
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.status is PlanStatus.FAILED
    assert result.report is not None
    assert not result.report.passed
    assert result.spec is None


# ---------------------------------------------------------------------------
# Requirement 9: validator error triggers repair, warning alone does not
# ---------------------------------------------------------------------------


def test_validator_error_triggers_repair(catalog):
    llm = FakeLLM(script=[_bad_part(valid_spec()), valid_spec()])
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.llm_calls == 2
    assert result.status is PlanStatus.OK
    repair_user = llm.calls[1]["user"]
    assert "UNKNOWN_PART" in repair_user
    assert "part_that_does_not_exist" in repair_user


def test_warning_only_does_not_trigger_repair(catalog):
    # A spec passing with no errors returns ok on the first call even if the
    # report carries warnings.
    llm = FakeLLM(script=[valid_spec()])
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.status is PlanStatus.OK
    assert result.llm_calls == 1
    assert result.report is not None
    assert result.report.passed


def test_repair_prompt_does_not_echo_previous_spec(catalog):
    # Token saving: the repair message carries the idea and the errors, never
    # the whole prior (large) spec.
    llm = FakeLLM(script=[_bad_part(valid_spec()), valid_spec()])
    Planner(llm, catalog).plan("a desk plant monitor")
    repair_user = llm.calls[1]["user"]
    assert "UNKNOWN_PART" in repair_user
    # The prior spec's unrelated fields must not be re-sent.
    assert "Plant Monitor" not in repair_user
    assert "connections" not in repair_user


def test_attempts_record_estimated_request_tokens(catalog):
    llm = FakeLLM(script=[valid_spec()])
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.attempts
    assert all(a.estimated_request_tokens > 0 for a in result.attempts)


def test_repair_prompt_filters_to_error_severity(catalog):
    # Warnings are advisory and must not be sent to the model as repair items.
    from sparkplan.models.spec import Issue
    from sparkplan.planner.phase3.planner import _repair_prompt

    issues = [
        Issue(code="E1", severity="error", message="bad pin", fix_hint="fix it"),
        Issue(code="W1", severity="warning", message="strapping", fix_hint="watch it"),
    ]
    prompt = _repair_prompt("a monitor", issues)
    assert "E1" in prompt and "bad pin" in prompt
    assert "W1" not in prompt


# ---------------------------------------------------------------------------
# Missing / empty inputs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("idea", ["", "   ", "\n\t "])
def test_empty_idea_refused_without_calling_llm(catalog, idea):
    llm = FakeLLM(script=[valid_spec()])
    result = Planner(llm, catalog).plan(idea)
    assert result.status is PlanStatus.REFUSED
    assert result.llm_calls == 0
    assert llm.calls == []


def test_transport_error_is_failed_not_refused(catalog):
    llm = FakeLLM(script=[LLMError("boom")])
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.status is PlanStatus.FAILED
    assert result.status is not PlanStatus.REFUSED
    assert any(i.code == "LLM_TRANSPORT" for i in result.issues)
