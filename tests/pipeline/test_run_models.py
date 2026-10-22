from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from sparkplan.models.spec import Issue
from sparkplan.pipeline.run_models import (
    RunResult,
    RunStageResult,
    RunStatus,
    StageStatus,
)


def make_issue(
    code: str = "TEST_ISSUE",
    severity: str = "warning",
    message: str = "test issue",
) -> Issue:
    return Issue(code=code, severity=severity, message=message)


def make_stage(
    name: str = "plan",
    status: StageStatus = StageStatus.PASSED,
    **kwargs: object,
) -> RunStageResult:
    if status in {StageStatus.SKIPPED, StageStatus.UNVERIFIED}:
        kwargs.setdefault("reason", "not attempted in this test")
    return RunStageResult(name=name, status=status, **kwargs)


def test_status_values_are_stable() -> None:
    assert [status.value for status in RunStatus] == [
        "running",
        "ok",
        "refused",
        "unsupported",
        "failed",
    ]
    assert [status.value for status in StageStatus] == [
        "passed",
        "failed",
        "skipped",
        "unverified",
    ]


def test_stage_serialization_includes_existing_issue() -> None:
    stage = make_stage(
        status=StageStatus.PASSED,
        issues=[make_issue()],
        evidence={"slicer_version": "PrusaSlicer-2.7.2"},
        artifacts={"base_stl": "base.stl", "guide": "GUIDE.md"},
        started_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T00:00:01Z",
        duration_ms=1000.0,
    )

    data = stage.to_dict()

    assert data["status"] == "passed"
    assert data["issues"][0]["code"] == "TEST_ISSUE"
    assert data["issues"][0]["severity"] == "warning"
    assert list(data["artifacts"]) == ["base_stl", "guide"]
    assert data["evidence"] == {"slicer_version": "PrusaSlicer-2.7.2"}


def test_run_serialization_is_deterministic_and_includes_issue() -> None:
    first = RunResult(
        run_id="run_1",
        status=RunStatus.OK,
        stages=[
            make_stage(
                artifacts={"guide": "GUIDE.md", "base_stl": "base.stl"},
            )
        ],
        issues=[make_issue()],
        artifacts={"trace": "trace.json", "spec": "spec.json"},
        created_at="2026-01-01T00:00:00Z",
    )
    second = RunResult(
        run_id="run_1",
        status=RunStatus.OK,
        stages=[
            make_stage(
                artifacts={"base_stl": "base.stl", "guide": "GUIDE.md"},
            )
        ],
        issues=[make_issue()],
        artifacts={"spec": "spec.json", "trace": "trace.json"},
        created_at="2026-01-01T00:00:00Z",
    )

    assert first.to_dict() == second.to_dict()
    assert first.to_json() == second.to_json()
    assert first.to_json().endswith("\n")
    assert json.loads(first.to_json())["status"] == "ok"
    assert list(first.to_dict()["artifacts"]) == ["spec", "trace"]


def test_duplicate_stage_names_are_rejected() -> None:
    with pytest.raises(ValidationError, match="stage names must be unique"):
        RunResult(
            run_id="run_1",
            status=RunStatus.FAILED,
            stages=[
                make_stage(name="plan"),
                make_stage(name="plan"),
            ],
        )


def test_ok_run_rejects_a_failed_stage() -> None:
    with pytest.raises(ValidationError, match="run status must be failed"):
        RunResult(
            run_id="run_1",
            status=RunStatus.OK,
            stages=[make_stage(name="geometry", status=StageStatus.FAILED)],
        )


def test_running_run_rejects_a_failed_stage() -> None:
    with pytest.raises(ValidationError, match="run status must be failed"):
        RunResult(
            run_id="run_1",
            status=RunStatus.RUNNING,
            stages=[make_stage(name="geometry", status=StageStatus.FAILED)],
        )


def test_failed_run_allows_a_failed_stage() -> None:
    result = RunResult(
        run_id="run_1",
        status=RunStatus.FAILED,
        stages=[make_stage(name="geometry", status=StageStatus.FAILED)],
    )

    assert result.failed_stages[0].name == "geometry"


@pytest.mark.parametrize("status", [StageStatus.SKIPPED, StageStatus.UNVERIFIED])
def test_skipped_and_unverified_stages_require_a_reason(status: StageStatus) -> None:
    with pytest.raises(ValidationError, match="requires a reason"):
        RunStageResult(name="compile", status=status)


def test_skipped_stage_is_not_represented_as_passed() -> None:
    result = RunResult(
        run_id="run_1",
        status=RunStatus.OK,
        stages=[make_stage(name="compile", status=StageStatus.SKIPPED)],
    )

    stage = result.stage("compile")

    assert stage is not None
    assert stage.status is StageStatus.SKIPPED
    assert stage.status is not StageStatus.PASSED
    assert result.status is RunStatus.OK


def test_passed_stage_rejects_error_issues() -> None:
    with pytest.raises(ValidationError, match="cannot contain error issues"):
        make_stage(
            status=StageStatus.PASSED,
            issues=[make_issue(severity="error")],
        )


def test_passed_stage_allows_warnings() -> None:
    stage = make_stage(status=StageStatus.PASSED, issues=[make_issue()])

    assert stage.status is StageStatus.PASSED


@pytest.mark.parametrize(
    "reference",
    [
        "/etc/passwd",
        "../outside",
        "a/../outside",
        "C:\\outside",
        "~/outside",
        "",
        "dir/",
        ".",
        "./dir",
        "dir//file",
        "dir\nfile",
    ],
)
def test_unsafe_artifact_references_are_rejected(reference: str) -> None:
    with pytest.raises(ValidationError, match="reference"):
        RunStageResult(
            name="geometry",
            status=StageStatus.PASSED,
            artifacts={"output": reference},
        )


def test_safe_nested_reference_is_accepted() -> None:
    stage = RunStageResult(
        name="firmware",
        status=StageStatus.PASSED,
        artifacts={"sketch": "sketch/sketch.ino"},
    )

    assert stage.artifacts == {"sketch": "sketch/sketch.ino"}


@pytest.mark.parametrize(
    "run_id",
    ["", ".", "..", "a/b", "a\\b", "a b", "a.b", "a" * 65, "é"],
)
def test_invalid_run_ids_are_rejected(run_id: str) -> None:
    with pytest.raises(ValidationError):
        RunResult(run_id=run_id, status=RunStatus.OK)


@pytest.mark.parametrize("status", [RunStatus.REFUSED, RunStatus.UNSUPPORTED])
def test_refusal_statuses_allow_no_stages(status: RunStatus) -> None:
    result = RunResult(run_id="run_1", status=status)

    assert result.stages == []


@pytest.mark.parametrize("status", [RunStatus.REFUSED, RunStatus.UNSUPPORTED])
def test_refusal_statuses_allow_only_a_failed_plan_stage(status: RunStatus) -> None:
    result = RunResult(
        run_id="run_1",
        status=status,
        stages=[make_stage(name="plan", status=StageStatus.FAILED)],
    )

    assert result.failed_stages[0].name == "plan"


def test_refused_run_rejects_a_failed_validation_stage() -> None:
    with pytest.raises(ValidationError, match="may only fail the plan stage"):
        RunResult(
            run_id="run_1",
            status=RunStatus.REFUSED,
            stages=[make_stage(name="validation", status=StageStatus.FAILED)],
        )
