"""Coverage for Phase 3 result accessors and remaining branch paths."""

from __future__ import annotations

import json

from sparkplan.llm.phase3 import FakeLLM, LLMError
from sparkplan.planner.phase3 import Planner, PlanStatus
from sparkplan.planner.phase3.models import Attempt, PlanResult
from tests.planner.phase3.conftest import valid_spec


def test_plan_result_accessors_ok(catalog):
    result = Planner(FakeLLM(script=[valid_spec()]), catalog).plan("plant monitor")
    assert result.ok is True
    assert result.passed is True
    assert result.error_issues() == []


def test_plan_result_accessors_failed(catalog):
    bad = json.loads(json.dumps(valid_spec()))
    bad["parts"][1]["part_id"] = "missing_part"
    result = Planner(FakeLLM(script=[bad] * 5), catalog).plan("plant monitor")
    assert result.ok is False
    assert result.passed is False
    assert result.report is not None
    assert result.error_issues()  # falls through to report.errors


def test_error_issues_without_report():
    # When no spec ever parsed, error_issues reads the raw issue list.
    from sparkplan.models.spec import Issue

    result = PlanResult(
        status=PlanStatus.FAILED,
        idea="x",
        issues=[Issue(code="X", severity="error", message="boom")],
    )
    assert [i.code for i in result.error_issues()] == ["X"]


def test_parse_error_extracts_part_ids(catalog):
    # A part_id field of the wrong type produces a parse error with part_ids.
    bad = {
        "status": "ok",
        "title": "t",
        "summary": "s",
        "parts": [{"instance_id": "x", "part_id": 5}],
    }
    llm = FakeLLM(script=[bad] * 5)
    result = Planner(llm, catalog).plan("plant monitor")
    assert result.status is PlanStatus.FAILED
    assert any(i.code == "SPEC_PARSE_ERROR" for i in result.issues)
    assert result.attempts[0].kind == "parse_error"


def test_no_response_path_yields_reason(catalog):
    # Exhausted before any response (empty script): failed with a reason.
    llm = FakeLLM(script=[LLMError("down")])
    result = Planner(llm, catalog).plan("plant monitor")
    assert result.status is PlanStatus.FAILED
    assert result.reasons


def test_attempt_defaults():
    a = Attempt(round=0, kind="ok")
    assert a.issues == []
    assert a.prompt_tokens == 0
    assert a.usage_present is False
