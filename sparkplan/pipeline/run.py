"""Canonical Phase 6 run orchestration.

The canonical run is deliberately separate from the legacy ``run_pipeline``
API. It plans a Phase 3 ``SupportedBuild``, persists that exact supported spec
and then drives the deterministic geometry, slicing, firmware, compile and
guide stages. Every exit path returns a complete ``RunResult`` with the same
seven canonical stages; stages that could not be attempted are ``skipped``,
and unavailable external tools are ``unverified``, never ``passed``.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from sparkplan.firmware import FirmwareError, FirmwareResult, compile_firmware, generate_firmware
from sparkplan.geometry.enclosure import EnclosureLayout
from sparkplan.geometry.generate import GeometryResult, generate_enclosure
from sparkplan.geometry.slicing import DEFAULT_TIMEOUT_S, SliceResult, verify_slice
from sparkplan.guide import RunVerification, render_supported_guide
from sparkplan.llm.phase3.client import LLMClient
from sparkplan.models.buildspec import SupportedBuild
from sparkplan.models.catalog import Catalog, get_catalog
from sparkplan.models.spec import Issue
from sparkplan.pipeline.run_models import (
    RunRefusal,
    RunResult,
    RunStageResult,
    RunStatus,
    StageStatus,
)
from sparkplan.pipeline.run_paths import RunExistsError, RunPaths, validate_run_id
from sparkplan.planner.phase3.models import PlanResult, PlanStatus
from sparkplan.planner.phase3.planner import Planner
from sparkplan.trace import new_run_id, utc_now

STAGE_ORDER: tuple[str, ...] = (
    "plan",
    "validation",
    "geometry",
    "slicing",
    "firmware_generation",
    "firmware_compile",
    "guide",
)

_MISSING_COMPILER_CODES = {
    "ARDUINO_CLI_MISSING",
    "ESP32_PLATFORM_MISSING",
    "LIBRARY_UNAVAILABLE",
}
_SECRET_RE = re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd)\b(\s*[=:]\s*)(\S+)")


def _issue(
    code: str,
    message: str,
    *,
    severity: str = "error",
    fix_hint: str = "",
) -> Issue:
    return Issue(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        message=message,
        part_ids=[],
        fix_hint=fix_hint,
    )


def _dedupe(issues: list[Issue]) -> list[Issue]:
    seen: set[str] = set()
    result: list[Issue] = []
    for issue in issues:
        key = issue.model_dump_json()
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result


def _has_errors(issues: list[Issue]) -> bool:
    return any(issue.severity == "error" for issue in issues)


def _reason(text: str | None, fallback: str) -> str:
    value = (text or fallback).strip()
    return value[:2000]


def _redact_text(text: str, root: Path | None = None, *, limit: int = 8000) -> str:
    """Remove run paths, the home directory, and obvious secrets from logs."""
    if root is not None:
        text = text.replace(str(root) + "/", "").replace(str(root), ".")
    text = text.replace(str(Path.home()), "~")
    text = _SECRET_RE.sub(r"\1\2[redacted]", text)
    if len(text) > limit:
        text = f"{text[:limit]}\n... [truncated]"
    return text


class _StageRecorder:
    """Collect unique stage records while preserving the canonical order."""

    def __init__(self) -> None:
        self.stages: dict[str, RunStageResult] = {}
        self.details: dict[str, dict[str, Any]] = {}

    def add(
        self,
        name: str,
        status: StageStatus,
        *,
        started_at: str | None = None,
        start: float | None = None,
        issues: list[Issue] | None = None,
        reason: str | None = None,
        evidence: dict[str, str] | None = None,
        artifacts: dict[str, str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> RunStageResult:
        duration = None if start is None else (time.perf_counter() - start) * 1000.0
        finished_at = utc_now() if start is not None else None
        stage = RunStageResult(
            name=name,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration,
            issues=list(issues or []),
            reason=reason,
            evidence=dict(evidence or {}),
            artifacts=dict(artifacts or {}),
        )
        self.stages[name] = stage
        self.details[name] = dict(details or {})
        return stage

    def ordered(self, *, reason: str) -> list[RunStageResult]:
        stages: list[RunStageResult] = []
        for name in STAGE_ORDER:
            stage = self.stages.get(name)
            if stage is None:
                stage = RunStageResult(name=name, status=StageStatus.SKIPPED, reason=reason)
            stages.append(stage)
        return stages


def _existing_artifacts(paths: RunPaths, names: tuple[str, ...]) -> dict[str, str]:
    inventory = paths.artifact_inventory()
    found: dict[str, str] = {}
    for name in names:
        path = paths.artifact(name)
        if path.is_file() and path.stat().st_size > 0:
            found[name] = inventory[name]
    return found


def _is_contained(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _relative_reference(value: str | Path | None, root: Path) -> str | None:
    if value is None:
        return None
    path = Path(value)
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    except ValueError:
        return None


def _slice_details(
    result: SliceResult,
    *,
    requested: bool,
    run_dir: Path,
) -> dict[str, Any]:
    return {
        "requested": requested,
        "slicer": Path(result.slicer).name if result.slicer else None,
        "version": result.version,
        "profile": Path(result.profile).name if result.profile else None,
        "reason": _redact_text(result.reason, run_dir) if result.reason else None,
        "parts": {
            name: {
                "status": part.status,
                "input": _relative_reference(part.input_path, run_dir),
                "output": _relative_reference(part.output_path, run_dir),
                "exit_code": part.exit_code,
                "duration_ms": part.duration_ms,
                "diagnostics": _redact_text(part.diagnostics, run_dir),
            }
            for name, part in result.parts.items()
        },
    }


def _slice_output_issues(result: SliceResult, run_dir: Path) -> list[Issue]:
    issues: list[Issue] = []
    if not result.parts:
        issues.append(
            _issue(
                "SLICE_OUTPUT_MISSING",
                "Slicing reported success but returned no base/lid results.",
                fix_hint="Inspect the slicer result; a status alone is not proof.",
            )
        )
        return issues
    for name, part in result.parts.items():
        if part.status != "passed":
            issues.append(
                _issue(
                    "SLICE_PART_NOT_PASSED",
                    f"Slicing reported success but part {name!r} is {part.status!r}.",
                    fix_hint="Inspect the per-part slicer result.",
                )
            )
            continue
        output = Path(part.output_path) if part.output_path else None
        if (
            output is None
            or not _is_contained(output, run_dir)
            or not output.is_file()
            or output.stat().st_size <= 0
        ):
            issues.append(
                _issue(
                    "SLICE_OUTPUT_MISSING",
                    f"Slicing reported success but the {name} G-code output is "
                    "missing, empty, or outside the run directory.",
                    fix_hint="Inspect the slicer result; a status alone is not proof.",
                )
            )
    return issues


def _slice_artifacts(result: SliceResult, run_dir: Path) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for name, part in result.parts.items():
        reference = _relative_reference(part.output_path, run_dir)
        if reference is None:
            continue
        path = run_dir / reference
        if path.is_file() and path.stat().st_size > 0:
            artifacts[f"{name}_gcode"] = reference
    return artifacts


def _compile_artifacts(sketch_dir: Path, run_dir: Path) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for key, name in (
        ("libraries", "libraries.txt"),
        ("library_manifest", "library_manifest.json"),
    ):
        path = sketch_dir / name
        if path.is_file() and path.stat().st_size > 0:
            artifacts[key] = path.relative_to(run_dir).as_posix()
    return artifacts


def _redact_issue(issue: Issue, root: Path) -> Issue:
    return issue.model_copy(
        update={
            "message": _redact_text(issue.message, root),
            "fix_hint": _redact_text(issue.fix_hint, root),
        }
    )


def _redacted_result(result: RunResult, root: Path) -> RunResult:
    """Return a report-safe copy with run paths and secrets redacted."""
    stages = [
        stage.model_copy(
            update={
                "issues": [_redact_issue(issue, root) for issue in stage.issues],
                "reason": (_redact_text(stage.reason, root) if stage.reason is not None else None),
            }
        )
        for stage in result.stages
    ]
    refusal = result.refusal
    if refusal is not None:
        refusal = refusal.model_copy(
            update={
                "reasons": [_redact_text(reason, root) for reason in refusal.reasons],
                "missing_capabilities": list(refusal.missing_capabilities),
                "closest_supported_alternative": _redact_text(
                    refusal.closest_supported_alternative, root
                ),
            }
        )
    return result.model_copy(
        update={
            "stages": stages,
            "issues": [_redact_issue(issue, root) for issue in result.issues],
            "refusal": refusal,
        }
    )


def _verification_value(stage: RunStageResult | None) -> bool | str:
    if stage is None or stage.status in {StageStatus.SKIPPED, StageStatus.UNVERIFIED}:
        return "skipped"
    return stage.status is StageStatus.PASSED


def _verification_for(stages: dict[str, RunStageResult]) -> RunVerification:
    """Map honest stage outcomes to the guide's four verification checks."""
    return RunVerification(
        validated=_verification_value(stages.get("validation")),
        compiled=_verification_value(stages.get("firmware_compile")),
        sliced=_verification_value(stages.get("slicing")),
        simulated="skipped",
    )


def _trace_payload(
    result: RunResult,
    *,
    idea: str,
    details: dict[str, dict[str, Any]],
    plan: PlanResult | None,
    verification: RunVerification,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "run_id": result.run_id,
        "status": result.status.value,
        "idea": idea,
        "created_at": result.created_at,
        "updated_at": result.updated_at,
        "finished_at": result.finished_at,
        "stage_order": list(STAGE_ORDER),
        "stages": [
            {**stage.to_dict(), "details": details.get(stage.name, {})} for stage in result.stages
        ],
        "issues": [issue.model_dump(mode="json") for issue in result.issues],
        "artifacts": result.artifacts,
        "verification": verification.as_dict(),
    }
    if plan is not None and plan.report is not None:
        payload["validation_report"] = {
            "passed": plan.report.passed,
            "issues": [issue.model_dump(mode="json") for issue in plan.report.issues],
            "timings_ms": dict(plan.report.timings_ms),
        }
    return payload


def _result(
    *,
    run_id: str,
    status: RunStatus,
    stages: list[RunStageResult],
    issues: list[Issue],
    artifacts: dict[str, str],
    created_at: str,
    refusal: RunRefusal | None = None,
) -> RunResult:
    finished_at = utc_now()
    return RunResult(
        run_id=run_id,
        status=status,
        stages=stages,
        issues=_dedupe(issues),
        artifacts=artifacts,
        refusal=refusal,
        created_at=created_at,
        updated_at=finished_at,
        finished_at=finished_at,
    )


def _run_status_for_plan(plan: PlanResult) -> RunStatus:
    if plan.status is PlanStatus.OK and plan.spec is None:
        return RunStatus.FAILED
    return {
        PlanStatus.OK: RunStatus.OK,
        PlanStatus.REFUSED: RunStatus.REFUSED,
        PlanStatus.UNSUPPORTED: RunStatus.UNSUPPORTED,
        PlanStatus.FAILED: RunStatus.FAILED,
    }[plan.status]


def _plan_reason(plan: PlanResult) -> str:
    if plan.status is PlanStatus.REFUSED:
        return _reason(plan.reasons[0] if plan.reasons else None, "planning refused the idea")
    if plan.status is PlanStatus.UNSUPPORTED:
        return _reason(plan.reasons[0] if plan.reasons else None, "the idea is unsupported")
    return _reason(plan.reasons[0] if plan.reasons else None, "planning failed")


def _refusal_for_plan(plan: PlanResult) -> RunRefusal | None:
    """Copy refusal details exactly as the planner reported them."""
    if plan.status not in {PlanStatus.REFUSED, PlanStatus.UNSUPPORTED}:
        return None
    return RunRefusal(
        reasons=list(plan.reasons),
        missing_capabilities=list(plan.missing_capabilities),
        closest_supported_alternative=plan.closest_supported_alternative,
    )


def run(
    idea: str,
    llm: LLMClient,
    *,
    run_id: str | None = None,
    runs_root: str | Path | None = None,
    catalog: Catalog | None = None,
    do_slice: bool = True,
    do_compile: bool = True,
    slicer: str | Path | None = None,
    slicer_profile: str | Path | None = None,
    slicer_timeout_s: float = DEFAULT_TIMEOUT_S,
    cli_path: str | None = None,
    extra_lib_install: bool = True,
) -> RunResult:
    """Plan and execute one canonical run.

    The run directory is created only after the planner returns a validated
    ``SupportedBuild``. Refusals, unsupported ideas and planning failures
    therefore leave the runs root untouched.
    """
    resolved_run_id = validate_run_id(run_id) if run_id is not None else new_run_id()
    paths = RunPaths(resolved_run_id, runs_root)
    if paths.run_dir.exists():
        raise RunExistsError(f"run directory already exists: {paths.run_dir}")

    cat = catalog if catalog is not None else get_catalog()
    created_at = utc_now()
    recorder = _StageRecorder()
    issues: list[Issue] = []

    plan_started = time.perf_counter()
    plan_started_at = utc_now()
    try:
        plan = Planner(llm, cat).plan(idea)
    except Exception as exc:
        issue = _issue(
            "PLAN_EXCEPTION",
            f"Planning raised {type(exc).__name__}: {exc}",
            fix_hint="Inspect the planner and LLM transport; no run artifacts were created.",
        )
        issues.append(issue)
        recorder.add(
            "plan",
            StageStatus.FAILED,
            started_at=plan_started_at,
            start=plan_started,
            issues=[issue],
            reason=_reason(str(exc), "planning raised an exception"),
            details={"error": _redact_text(f"{type(exc).__name__}: {exc}", paths.run_dir)},
        )
        stages = recorder.ordered(reason="not reached because planning raised an exception")
        return _result(
            run_id=resolved_run_id,
            status=RunStatus.FAILED,
            stages=stages,
            issues=issues,
            artifacts={},
            created_at=created_at,
        )

    plan_issues = list(plan.issues)
    if plan.status is PlanStatus.OK and plan.spec is None:
        plan_issues.append(
            _issue(
                "PLAN_SPEC_MISSING",
                "The planner reported success without a supported spec.",
                fix_hint="This is a SparkPlan bug; report it.",
            )
        )
    issues.extend(plan_issues)
    plan_passed = plan.status is PlanStatus.OK and plan.spec is not None
    plan_details = {
        "plan_status": plan.status.value,
        "repair_rounds": plan.repair_rounds,
        "llm_calls": plan.llm_calls,
        "total_prompt_tokens": plan.total_prompt_tokens,
        "total_completion_tokens": plan.total_completion_tokens,
        "attempts": [
            {
                "round": attempt.round,
                "kind": attempt.kind,
                "detail": attempt.detail,
                "issues": [issue.model_dump(mode="json") for issue in attempt.issues],
            }
            for attempt in plan.attempts
        ],
    }
    recorder.add(
        "plan",
        StageStatus.PASSED if plan_passed else StageStatus.FAILED,
        started_at=plan_started_at,
        start=plan_started,
        issues=plan_issues,
        reason=None if plan_passed else _plan_reason(plan),
        evidence={
            "plan_status": plan.status.value,
            "repair_rounds": str(plan.repair_rounds),
            "llm_calls": str(plan.llm_calls),
        },
        details=plan_details,
    )

    if not plan_passed:
        validation_reason = "not reached because planning did not produce a supported spec"
        if plan.report is not None and plan.status is PlanStatus.FAILED:
            report = plan.report
            issues.extend(report.issues)
            validation_status = StageStatus.PASSED if report.passed else StageStatus.FAILED
            validation_reason = (
                "the spec passed validation but planning still failed"
                if report.passed
                else "the final planning attempt did not pass validation"
            )
            recorder.add(
                "validation",
                validation_status,
                started_at=plan_started_at,
                start=plan_started,
                issues=report.issues,
                reason=None if report.passed else validation_reason,
                evidence={"report_passed": str(report.passed).lower()},
                details={
                    "path": "planner.plan",
                    "timings_ms": dict(report.timings_ms),
                    "by_code": report.by_code(),
                },
            )
            validation_reason = (
                "not reached because planning did not produce a supported spec"
                if report.passed
                else "not reached because planning failed validation"
            )
        else:
            recorder.add(
                "validation",
                StageStatus.SKIPPED,
                reason=validation_reason,
            )
        stages = recorder.ordered(reason=validation_reason)
        return _result(
            run_id=resolved_run_id,
            status=_run_status_for_plan(plan),
            stages=stages,
            issues=issues,
            artifacts={},
            created_at=created_at,
            refusal=_refusal_for_plan(plan),
        )

    assert plan.spec is not None
    spec: SupportedBuild = plan.spec
    report = plan.report
    if report is None:
        issue = _issue(
            "VALIDATION_REPORT_MISSING",
            "The planner returned a supported spec without its validation report.",
            fix_hint="This is a SparkPlan bug; report it.",
        )
        issues.append(issue)
        recorder.add(
            "validation",
            StageStatus.FAILED,
            started_at=plan_started_at,
            start=plan_started,
            issues=[issue],
            reason=issue.message,
        )
        stages = recorder.ordered(reason="not reached because validation evidence was missing")
        return _result(
            run_id=resolved_run_id,
            status=RunStatus.FAILED,
            stages=stages,
            issues=issues,
            artifacts={},
            created_at=created_at,
        )

    try:
        paths.claim()
        paths.write_json("spec", spec.model_dump(mode="json"))
    except Exception as exc:
        issue = _issue(
            "RUN_SPEC_WRITE_FAILED",
            f"Could not persist the supported spec: {exc}",
            fix_hint="Inspect the runs directory permissions and available space.",
        )
        issues.append(issue)
        recorder.add(
            "validation",
            StageStatus.FAILED,
            started_at=plan_started_at,
            start=plan_started,
            issues=[issue],
            reason=issue.message,
        )
        stages = recorder.ordered(reason="not reached because the supported spec was not persisted")
        return _result(
            run_id=resolved_run_id,
            status=RunStatus.FAILED,
            stages=stages,
            issues=issues,
            artifacts={},
            created_at=created_at,
        )

    validation_issues = list(report.issues)
    issues.extend(validation_issues)
    validation_status = StageStatus.PASSED if report.passed else StageStatus.FAILED
    recorder.add(
        "validation",
        validation_status,
        started_at=plan_started_at,
        start=plan_started,
        issues=validation_issues,
        reason=None if report.passed else "the supported spec did not pass validation",
        evidence={"report_passed": str(report.passed).lower()},
        artifacts={"spec": paths.artifact_inventory()["spec"]},
        details={
            "path": "planner.plan",
            "timings_ms": dict(report.timings_ms),
            "by_code": report.by_code(),
        },
    )

    if not report.passed:
        stages = recorder.ordered(reason="not reached because validation failed")
        return _result(
            run_id=resolved_run_id,
            status=RunStatus.FAILED,
            stages=stages,
            issues=issues,
            artifacts=_existing_artifacts(paths, ("spec",)),
            created_at=created_at,
        )

    geometry_started = time.perf_counter()
    geometry_started_at = utc_now()
    geometry: GeometryResult | None = None
    try:
        geometry = generate_enclosure(spec, cat, out_dir=paths.run_dir)
    except Exception as exc:
        issue = _issue(
            "GEOMETRY_EXCEPTION",
            f"Enclosure generation raised {type(exc).__name__}: {exc}",
            fix_hint="Inspect the geometry engine and the validated layout.",
        )
        issues.append(issue)
        geometry_issues = [issue]
        geometry_status = StageStatus.FAILED
        geometry_reason = _reason(str(exc), "geometry generation raised an exception")
        geometry_artifacts = _existing_artifacts(paths, ("base_stl", "lid_stl", "assembled_stl"))
        geometry_details: dict[str, Any] = {"error": f"{type(exc).__name__}: {exc}"}
        geometry_layout: EnclosureLayout | None = None
    else:
        geometry_issues = list(geometry.issues)
        issues.extend(geometry_issues)
        geometry_artifacts = _existing_artifacts(paths, ("base_stl", "lid_stl", "assembled_stl"))
        report_path = geometry.report_path
        geometry_report_exists = bool(
            report_path is not None
            and Path(report_path).is_file()
            and Path(report_path).is_relative_to(paths.run_dir)
        )
        if geometry_report_exists:
            geometry_artifacts["geometry_report"] = "geometry_report.json"
        geometry_layout = geometry.layout
        geometry_details = {
            "engine": geometry.engine,
            "exported": [
                path.relative_to(paths.run_dir).as_posix()
                for path in geometry.exported
                if Path(path).is_relative_to(paths.run_dir)
            ],
            "error_codes": [issue.code for issue in geometry.error_issues],
        }
        if geometry.error_issues or geometry.layout is None:
            geometry_status = StageStatus.FAILED
            geometry_reason = (
                geometry.error_issues[0].message
                if geometry.error_issues
                else "geometry did not produce a layout"
            )
        elif not {"base_stl", "lid_stl", "assembled_stl"}.issubset(geometry_artifacts):
            engine_unavailable = any(
                issue.code == "GEOMETRY_ENGINE_UNAVAILABLE" for issue in geometry_issues
            )
            if engine_unavailable:
                geometry_status = StageStatus.UNVERIFIED
                geometry_reason = "no solid-modelling engine was available, so no STL was exported"
            else:
                issue = _issue(
                    "GEOMETRY_OUTPUT_MISSING",
                    "Geometry reported success but one or more expected STL "
                    "files are missing or empty.",
                    fix_hint="Inspect geometry_report.json and regenerate the enclosure.",
                )
                issues.append(issue)
                geometry_issues.append(issue)
                geometry_status = StageStatus.FAILED
                geometry_reason = issue.message
        else:
            geometry_status = StageStatus.PASSED
            geometry_reason = None

    recorder.add(
        "geometry",
        geometry_status,
        started_at=geometry_started_at,
        start=geometry_started,
        issues=geometry_issues,
        reason=geometry_reason,
        evidence={"engine": geometry.engine or "unavailable"} if geometry is not None else {},
        artifacts=geometry_artifacts,
        details=geometry_details,
    )

    slice_requested = bool(do_slice)
    slice_result: SliceResult | None = None
    if geometry_status is not StageStatus.PASSED:
        slice_reason = "not reached because valid enclosure STLs were not exported"
        recorder.add(
            "slicing",
            StageStatus.SKIPPED,
            reason=slice_reason,
            details={"requested": slice_requested},
        )
    elif not do_slice:
        recorder.add(
            "slicing",
            StageStatus.SKIPPED,
            reason="slicing was not requested",
            details={"requested": False},
        )
    else:
        slice_started = time.perf_counter()
        slice_started_at = utc_now()
        try:
            slice_result = verify_slice(
                paths.artifact("base_stl"),
                paths.artifact("lid_stl"),
                output_dir=paths.run_dir,
                slicer=slicer,
                profile=slicer_profile,
                timeout_s=slicer_timeout_s,
                enabled=True,
            )
        except Exception as exc:
            issue = _issue(
                "SLICE_EXCEPTION",
                f"Slicing raised {type(exc).__name__}: {exc}",
                fix_hint="Inspect the slicer configuration.",
            )
            issues.append(issue)
            recorder.add(
                "slicing",
                StageStatus.FAILED,
                started_at=slice_started_at,
                start=slice_started,
                issues=[issue],
                reason=_reason(str(exc), "slicing raised an exception"),
                details={
                    "requested": do_slice,
                    "error": _redact_text(f"{type(exc).__name__}: {exc}", paths.run_dir),
                },
            )
        else:
            slice_issues = list(slice_result.issues)
            issues.extend(slice_issues)
            if slice_result.status == "passed":
                output_issues = _slice_output_issues(slice_result, paths.run_dir)
                if output_issues:
                    issues.extend(output_issues)
                    slice_issues.extend(output_issues)
                    slice_status = StageStatus.FAILED
                    slice_reason = output_issues[0].message
                elif _has_errors(slice_issues):
                    slice_status = StageStatus.FAILED
                    slice_reason = _reason(
                        next(issue.message for issue in slice_issues if issue.severity == "error"),
                        "slicing reported success alongside errors",
                    )
                else:
                    slice_status = StageStatus.PASSED
                    slice_reason = None
            elif slice_result.status == "failed":
                slice_status = StageStatus.FAILED
                slice_reason = _reason(
                    slice_result.reason or (slice_issues[0].message if slice_issues else None),
                    "slicing failed",
                )
            elif slice_result.status == "unverified":
                slice_status = StageStatus.UNVERIFIED
                slice_reason = _reason(
                    slice_result.reason or (slice_issues[0].message if slice_issues else None),
                    "slicing could not be verified",
                )
            else:
                slice_status = StageStatus.SKIPPED
                slice_reason = _reason(slice_result.reason, "slicing was not requested")
            recorder.add(
                "slicing",
                slice_status,
                started_at=slice_started_at,
                start=slice_started,
                issues=slice_issues,
                reason=slice_reason,
                evidence={
                    "requested": str(do_slice).lower(),
                    "slicer": (
                        Path(slice_result.slicer).name if slice_result.slicer else "unavailable"
                    ),
                    "version": slice_result.version or "unknown",
                },
                artifacts=_slice_artifacts(slice_result, paths.run_dir),
                details=_slice_details(
                    slice_result,
                    requested=do_slice,
                    run_dir=paths.run_dir,
                ),
            )

    firmware_started = time.perf_counter()
    firmware_started_at = utc_now()
    generated: FirmwareResult | None = None
    firmware_for_guide: FirmwareResult | None = None
    firmware_stage_status = StageStatus.FAILED
    try:
        generated = generate_firmware(spec, cat)
    except FirmwareError as exc:
        firmware_issues = list(getattr(exc, "issues", []) or [])
        if not firmware_issues:
            firmware_issues = [
                _issue(
                    "FIRMWARE_FAILED",
                    str(exc),
                    fix_hint="Inspect the catalog firmware template and the validated build.",
                )
            ]
        issues.extend(firmware_issues)
        recorder.add(
            "firmware_generation",
            StageStatus.FAILED,
            started_at=firmware_started_at,
            start=firmware_started,
            issues=firmware_issues,
            reason=_reason(str(exc), "firmware generation failed"),
            details={"error": _redact_text(f"{type(exc).__name__}: {exc}", paths.run_dir)},
        )
    except Exception as exc:
        issue = _issue(
            "FIRMWARE_EXCEPTION",
            f"Firmware generation raised {type(exc).__name__}: {exc}",
            fix_hint="Inspect the behavior generator and catalog templates.",
        )
        issues.append(issue)
        recorder.add(
            "firmware_generation",
            StageStatus.FAILED,
            started_at=firmware_started_at,
            start=firmware_started,
            issues=[issue],
            reason=_reason(str(exc), "firmware generation raised an exception"),
            details={"error": _redact_text(f"{type(exc).__name__}: {exc}", paths.run_dir)},
        )
    else:
        firmware_issues = list(generated.issues)
        issues.extend(firmware_issues)
        if _has_errors(generated.issues):
            recorder.add(
                "firmware_generation",
                StageStatus.FAILED,
                started_at=firmware_started_at,
                start=firmware_started,
                issues=firmware_issues,
                reason=_reason(
                    next(
                        (issue.message for issue in firmware_issues if issue.severity == "error"),
                        None,
                    ),
                    "firmware generation reported errors",
                ),
            )
        elif not generated.sketch.strip():
            issue = _issue(
                "FIRMWARE_EMPTY",
                "Firmware generation returned an empty sketch.",
                fix_hint="Inspect the catalog firmware template.",
            )
            issues.append(issue)
            firmware_issues.append(issue)
            recorder.add(
                "firmware_generation",
                StageStatus.FAILED,
                started_at=firmware_started_at,
                start=firmware_started,
                issues=firmware_issues,
                reason=issue.message,
            )
        else:
            try:
                paths.write_text("sketch", generated.sketch)
            except Exception as exc:
                issue = _issue(
                    "FIRMWARE_WRITE_FAILED",
                    f"Could not persist the firmware sketch: {exc}",
                    fix_hint="Inspect the runs directory permissions and available space.",
                )
                issues.append(issue)
                firmware_issues.append(issue)
                recorder.add(
                    "firmware_generation",
                    StageStatus.FAILED,
                    started_at=firmware_started_at,
                    start=firmware_started,
                    issues=firmware_issues,
                    reason=issue.message,
                )
            else:
                firmware_stage_status = StageStatus.PASSED
                firmware_for_guide = generated
                recorder.add(
                    "firmware_generation",
                    StageStatus.PASSED,
                    started_at=firmware_started_at,
                    start=firmware_started,
                    issues=firmware_issues,
                    artifacts={"sketch": paths.artifact_inventory()["sketch"]},
                    evidence={"libraries": str(len(generated.libraries))},
                    details={
                        "libraries": list(generated.libraries),
                        "readings": len(generated.readings),
                        "characters": len(generated.sketch),
                    },
                )

    if firmware_stage_status is not StageStatus.PASSED or generated is None:
        recorder.add(
            "firmware_compile",
            StageStatus.SKIPPED,
            reason="not reached because firmware generation did not produce a sketch",
            details={"requested": do_compile},
        )
    elif not do_compile:
        recorder.add(
            "firmware_compile",
            StageStatus.SKIPPED,
            reason="firmware compilation was not requested",
            details={"requested": False},
        )
    else:
        compile_started = time.perf_counter()
        compile_started_at = utc_now()
        try:
            compiled = compile_firmware(
                generated,
                paths.artifact("sketch").parent,
                cli_path=cli_path,
                extra_lib_install=extra_lib_install,
                sketch_name="sketch.ino",
                write_sketch_file=False,
            )
        except Exception as exc:
            issue = _issue(
                "COMPILE_EXCEPTION",
                f"Firmware compilation raised {type(exc).__name__}: {exc}",
                fix_hint="Inspect the arduino-cli installation and the sketch.",
            )
            issues.append(issue)
            recorder.add(
                "firmware_compile",
                StageStatus.FAILED,
                started_at=compile_started_at,
                start=compile_started,
                issues=[issue],
                reason=_reason(str(exc), "firmware compilation raised an exception"),
                details={"error": _redact_text(f"{type(exc).__name__}: {exc}", paths.run_dir)},
            )
        else:
            prior = list(generated.issues)
            compile_issues = [issue for issue in compiled.issues if issue not in prior]
            issues.extend(compile_issues)
            sketch_path = paths.artifact("sketch")
            sketch_ok = sketch_path.is_file() and sketch_path.stat().st_size > 0
            if compiled.compile_status == "ok" and _has_errors(compile_issues):
                compile_status = StageStatus.FAILED
                compile_reason = _reason(
                    compile_issues[0].message if compile_issues else None,
                    "compilation reported errors",
                )
            elif compiled.compile_status == "ok" and not sketch_ok:
                issue = _issue(
                    "COMPILE_OUTPUT_MISSING",
                    "Compilation reported success but the required sketch "
                    "artifact is missing or empty.",
                    fix_hint="Inspect the arduino-cli result and the persisted sketch.",
                )
                issues.append(issue)
                compile_issues.append(issue)
                compile_status = StageStatus.FAILED
                compile_reason = issue.message
            elif compiled.compile_status == "ok":
                compile_status = StageStatus.PASSED
                compile_reason = None
            elif compiled.compile_status == "skipped":
                compile_errors = [issue for issue in compile_issues if issue.severity == "error"]
                if compile_errors and any(
                    issue.code not in _MISSING_COMPILER_CODES for issue in compile_errors
                ):
                    compile_status = StageStatus.FAILED
                    compile_reason = _reason(
                        compile_issues[0].message if compile_issues else None,
                        "compilation could not be completed",
                    )
                else:
                    compile_status = StageStatus.UNVERIFIED
                    compile_reason = _reason(
                        compile_issues[0].message if compile_issues else None,
                        "arduino-cli or a required toolchain dependency is unavailable",
                    )
            else:
                compile_status = StageStatus.FAILED
                compile_reason = _reason(
                    compile_issues[0].message if compile_issues else None,
                    "firmware compilation failed",
                )
            firmware_for_guide = compiled
            recorder.add(
                "firmware_compile",
                compile_status,
                started_at=compile_started_at,
                start=compile_started,
                issues=compile_issues,
                reason=compile_reason,
                evidence={
                    "compile_status": compiled.compile_status,
                    "arduino_cli": (
                        "available" if compiled.compile_status != "skipped" else "unavailable"
                    ),
                },
                artifacts=_compile_artifacts(sketch_path.parent, paths.run_dir),
                details={
                    "compile_status": compiled.compile_status,
                    "compile_log": _redact_text(compiled.compile_log, paths.run_dir),
                },
            )

    guide_started = time.perf_counter()
    guide_started_at = utc_now()
    guide_layout = (
        geometry_layout if geometry_status in {StageStatus.PASSED, StageStatus.UNVERIFIED} else None
    )
    guide_verification = _verification_for(recorder.stages)
    try:
        guide = render_supported_guide(
            spec,
            cat,
            guide_layout,
            firmware_for_guide,
            issues,
            verification=guide_verification,
        )
        paths.write_text("guide", guide)
    except Exception as exc:
        issue = _issue(
            "GUIDE_FAILED",
            f"Guide generation or persistence failed: {exc}",
            fix_hint="Inspect the guide renderer and the runs directory.",
        )
        issues.append(issue)
        recorder.add(
            "guide",
            StageStatus.FAILED,
            started_at=guide_started_at,
            start=guide_started,
            issues=[issue],
            reason=_reason(str(exc), "guide generation failed"),
            details={"error": _redact_text(f"{type(exc).__name__}: {exc}", paths.run_dir)},
        )
    else:
        recorder.add(
            "guide",
            StageStatus.PASSED,
            started_at=guide_started_at,
            start=guide_started,
            artifacts={"guide": paths.artifact_inventory()["guide"]},
            evidence={"characters": str(len(guide))},
            details={"characters": len(guide)},
        )

    final_stages = recorder.ordered(reason="not reached before the run finished")
    run_status = (
        RunStatus.FAILED
        if any(stage.status is StageStatus.FAILED for stage in final_stages)
        else RunStatus.OK
    )
    artifacts = _existing_artifacts(
        paths,
        ("spec", "base_stl", "lid_stl", "assembled_stl", "sketch", "guide"),
    )
    artifacts["report"] = paths.artifact_inventory()["report"]
    artifacts["trace"] = paths.artifact_inventory()["trace"]
    result = _redacted_result(
        _result(
            run_id=resolved_run_id,
            status=run_status,
            stages=final_stages,
            issues=issues,
            artifacts=artifacts,
            created_at=created_at,
        ),
        paths.run_dir,
    )
    paths.write_json("report", result)
    paths.write_json(
        "trace",
        _trace_payload(
            result,
            idea=idea,
            details=recorder.details,
            plan=plan,
            verification=_verification_for(recorder.stages),
        ),
    )
    return result
