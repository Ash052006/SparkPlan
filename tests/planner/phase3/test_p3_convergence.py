"""Phase 3 convergence / repair-feedback tests. Offline: FakeLLM only.

Covers the desk-monitor convergence failure found live: the model omitted
connections/layout and added an unrequested buzzer because repair regenerated
the build from scratch. These tests pin the improved repair contract.
"""

from __future__ import annotations

import json
from pathlib import Path

from sparkplan.llm.phase3 import FakeLLM, LLMUsage
from sparkplan.planner.phase3 import MAX_LLM_CALLS, Planner, PlanStatus
from tests.planner.phase3.conftest import as_supported, valid_spec

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "phase3"


def _desk_failing() -> dict:
    return json.loads((FIXTURES / "attempts" / "desk_monitor_missing_wiring.json").read_text())


# ---------------------------------------------------------------------------
# 1. Targeted repair feedback for each error class
# ---------------------------------------------------------------------------


def test_missing_power_ground_layout_feedback_is_targeted(catalog):
    llm = FakeLLM(script=[_desk_failing(), valid_spec()])
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.status is PlanStatus.OK
    assert result.llm_calls == 2
    repair = llm.calls[1]["user"]
    # The specific missing wiring/ground/mount errors must be named.
    assert "NO_POWER" in repair
    assert "NO_GROUND" in repair
    assert "NO_MOUNT_ZONE" in repair
    assert "PIN_UNCONNECTED" in repair
    # The affected instance ids provide component context.
    assert "bme" in repair and "oled" in repair


def test_repair_feedback_names_every_required_section(catalog):
    llm = FakeLLM(script=[_desk_failing(), valid_spec()])
    Planner(llm, catalog).plan("a desk plant monitor")
    repair = llm.calls[1]["user"]
    assert "CURRENT_SPEC" in repair
    assert "layout entry" in repair
    assert "EVERY remaining pin" in repair


# ---------------------------------------------------------------------------
# 2. Repairing one issue preserves previously valid connections
# ---------------------------------------------------------------------------


def test_repair_anchor_preserves_valid_connections(catalog):
    # The candidate spec already has valid soil connections; they must be echoed
    # back so a repair cannot silently drop them.
    failing = _desk_failing()
    llm = FakeLLM(script=[failing, valid_spec()])
    Planner(llm, catalog).plan("a desk plant monitor")
    repair = llm.calls[1]["user"].replace(" ", "")
    # The valid soil AOUT->D32 connection is present in the anchor.
    assert '"from_pin":"AOUT"' in repair
    assert '"to_pin":"D32"' in repair
    assert '"instance_id":"soil"' in repair


# ---------------------------------------------------------------------------
# 3. No unrequested components
# ---------------------------------------------------------------------------


def test_repair_instruction_forbids_unrequested_components(catalog):
    llm = FakeLLM(script=[_desk_failing(), valid_spec()])
    Planner(llm, catalog).plan("a desk plant monitor")
    repair = llm.calls[1]["user"]
    assert "Do NOT add any component the user did not ask for" in repair


def test_unrequested_component_is_not_accepted(catalog):
    # A first response that adds a component the idea never asked for is not a
    # valid build on its own if it fails validation; the planner never returns
    # ok for it without the validators passing.
    llm = FakeLLM(script=[_desk_failing()] * 10)
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.status is PlanStatus.FAILED
    assert result.spec is None


# ---------------------------------------------------------------------------
# 4. Bounded termination
# ---------------------------------------------------------------------------


def test_repeated_invalid_terminates_within_bound(catalog):
    llm = FakeLLM(script=[_desk_failing()] * 20)
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.status is PlanStatus.FAILED
    assert result.llm_calls == MAX_LLM_CALLS
    assert len(llm.calls) == MAX_LLM_CALLS
    assert result.report is not None and not result.report.passed


# ---------------------------------------------------------------------------
# 5. Truncated-but-parseable accepted only if parse+validation pass
# ---------------------------------------------------------------------------


def test_truncated_parseable_but_invalid_is_not_ok(catalog):
    # finish_reason=length with complete JSON that FAILS validation must still
    # trigger repair, never ok.
    llm = FakeLLM(script=[(_desk_failing(), LLMUsage(10, 10, True), "length")] * 10)
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.status is PlanStatus.FAILED
    assert result.report is not None and not result.report.passed
    assert result.attempts[0].kind == "validation_error"


def test_truncated_parseable_and_valid_is_ok(catalog):
    llm = FakeLLM(script=[(valid_spec(), LLMUsage(10, 10, True), "length")])
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.status is PlanStatus.OK


# ---------------------------------------------------------------------------
# 6. Strict success invariant
# ---------------------------------------------------------------------------


def test_invalid_supported_build_never_returns_ok(catalog):
    llm = FakeLLM(script=[_desk_failing()] * 10)
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.status is not PlanStatus.OK
    assert result.status is PlanStatus.FAILED


def test_validator_error_report_is_returned_on_failure(catalog):
    llm = FakeLLM(script=[_desk_failing()] * 10)
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.report is not None
    assert any(i.code == "PIN_UNCONNECTED" for i in result.report.errors)


def test_candidate_anchor_is_a_valid_supported_build(catalog):
    # Sanity: the failing fixture parses to a SupportedBuild so the planner can
    # anchor repairs on it (it is schema-valid, just electrically incomplete).
    spec = as_supported(_desk_failing())
    assert spec.status == "ok"
