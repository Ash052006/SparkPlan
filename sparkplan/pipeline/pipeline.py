"""Pipeline orchestration: plan -> validate -> geometry -> firmware -> guide.

Each stage lives in its own package and is callable on its own; the
pipeline only sequences them and converts stage failures into Issues.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sparkplan.catalog import Catalog, load_catalog
from sparkplan.firmware import FirmwareError, FirmwareResult, compile_firmware, generate_firmware
from sparkplan.geometry import (
    EnclosureMeshes,
    EnclosurePlan,
    GeometryEngineError,
    SliceResult,
    build_enclosure,
    compute_enclosure_plan,
    verify_slice,
)
from sparkplan.geometry.slicing import slice_details
from sparkplan.guide import render_guide
from sparkplan.llm.client import LLMClient
from sparkplan.models.spec import BuildSpec, Issue, PlanResult, PlanStatus
from sparkplan.planner import Planner
from sparkplan.trace import (
    RunTrace,
    claim_trace_path,
    complete_trace_stages,
    create_trace,
    new_run_id,
    utc_now,
    write_trace,
)


@dataclass
class PipelineResult:
    status: Literal["ok", "refused", "failed"]
    idea: str
    plan: PlanResult | None = None
    spec: BuildSpec | None = None
    enclosure_plan: EnclosurePlan | None = None
    enclosure: EnclosureMeshes | None = None
    firmware: FirmwareResult | None = None
    guide: str | None = None
    slice_result: SliceResult | None = None
    issues: list[Issue] = field(default_factory=list)
    run_id: str | None = None
    trace_path: Path | None = None
    trace_write_error: str | None = None

    @property
    def refusal_reasons(self) -> list[str]:
        if self.plan and self.plan.refusal:
            return self.plan.refusal.reasons
        return []


def run_pipeline(
    idea: str,
    llm: LLMClient,
    catalog: Catalog | None = None,
    out_dir: str | Path | None = None,
    do_compile: bool = False,
    trace_path: str | Path | None = None,
    do_slice: bool = False,
) -> PipelineResult:
    catalog = catalog or load_catalog()
    run_id = new_run_id()
    out_path = Path(out_dir) if out_dir is not None else None
    resolved_trace_path: Path | None = None
    if trace_path is not None:
        resolved_trace_path = claim_trace_path(Path(trace_path))
    elif out_path is not None:
        candidate = out_path / "trace.json"
        try:
            resolved_trace_path = claim_trace_path(candidate)
        except FileExistsError:
            resolved_trace_path = claim_trace_path(out_path / f"trace.{run_id}.json")

    trace: RunTrace | None = None
    if resolved_trace_path is not None:
        trace = create_trace(run_id, idea, utc_now())
        try:
            write_trace(trace, resolved_trace_path)
        except Exception:
            with contextlib.suppress(OSError):
                if resolved_trace_path.stat().st_size == 0:
                    resolved_trace_path.unlink(missing_ok=True)
            trace = None
            resolved_trace_path = None

    result: PipelineResult | None = None
    exc_info: BaseException | None = None
    active_stage: str | None = None
    try:
        planner = Planner(llm, catalog)
        plan_started = time.perf_counter()
        plan_started_at = utc_now()
        active_stage = "plan"
        try:
            plan = planner.plan(idea)
        except Exception as exc:
            if trace is not None:
                trace.record_stage(
                    "plan",
                    "failed",
                    started_at=plan_started_at,
                    finished_at=utc_now(),
                    duration_ms=(time.perf_counter() - plan_started) * 1000.0,
                    issues=[],
                    details={"error": f"{type(exc).__name__}: {exc}"},
                )
                _persist_trace(trace, resolved_trace_path)
            raise
        plan_issues = list(plan.issues)
        plan_status = (
            "passed" if plan.status is PlanStatus.OK and not _has_errors(plan_issues) else "failed"
        )
        plan_finished_at = utc_now()
        if trace is not None:
            trace.record_stage(
                "plan",
                plan_status,
                started_at=plan_started_at,
                finished_at=plan_finished_at,
                duration_ms=(time.perf_counter() - plan_started) * 1000.0,
                issues=plan_issues,
                details={
                    "plan_status": plan.status.value,
                    "repair_rounds": plan.repair_rounds,
                },
            )
            trace.record_stage(
                "validation",
                plan_status,
                started_at=plan_started_at,
                finished_at=plan_finished_at,
                duration_ms=(time.perf_counter() - plan_started) * 1000.0,
                issues=plan_issues,
                details={
                    "path": "Planner.run_validators",
                    "plan_status": plan.status.value,
                },
                reason=None if plan_status == "passed" else "plan did not pass validation",
            )
            _persist_trace(trace, resolved_trace_path)
        active_stage = None

        if plan.status is not PlanStatus.OK or plan.spec is None:
            status = "refused" if plan.status is PlanStatus.REFUSED else "failed"
            result = PipelineResult(
                status=status,
                idea=idea,
                plan=plan,
                issues=plan_issues,
                run_id=run_id,
                trace_path=resolved_trace_path,
            )
            if trace is not None:
                reason = (
                    "not reached because planning was refused"
                    if status == "refused"
                    else "not reached because planning failed"
                )
                trace.record_stage(
                    "slicing",
                    "skipped",
                    details={"requested": do_slice},
                    reason=reason,
                )
                complete_trace_stages(trace, reason=reason)
                trace.status = "failed"
                _persist_trace(trace, resolved_trace_path)
            return result

        spec = plan.spec
        issues = list(plan.issues)

        # Geometry.
        geometry_started = time.perf_counter()
        geometry_started_at = utc_now()
        active_stage = "geometry_layout"
        try:
            enclosure_plan = compute_enclosure_plan(spec, catalog)
        except GeometryEngineError as exc:
            issues.append(Issue(code="GEOMETRY_FAILED", severity="error", message=str(exc)))
            if trace is not None:
                trace.record_stage(
                    "geometry_layout",
                    "failed",
                    started_at=geometry_started_at,
                    finished_at=utc_now(),
                    duration_ms=(time.perf_counter() - geometry_started) * 1000.0,
                    issues=[Issue(code="GEOMETRY_FAILED", severity="error", message=str(exc))],
                )
                reason = "not reached because geometry layout failed"
                trace.record_stage(
                    "slicing",
                    "skipped",
                    details={"requested": do_slice},
                    reason=reason,
                )
                complete_trace_stages(trace, reason=reason)
                _persist_trace(trace, resolved_trace_path)
            active_stage = None
            result = PipelineResult(
                status="failed",
                idea=idea,
                plan=plan,
                spec=spec,
                issues=issues,
                run_id=run_id,
                trace_path=resolved_trace_path,
            )
            return result

        active_stage = None
        if trace is not None:
            trace.record_stage(
                "geometry_layout",
                "passed",
                started_at=geometry_started_at,
                finished_at=utc_now(),
                duration_ms=(time.perf_counter() - geometry_started) * 1000.0,
                details={"placements": len(enclosure_plan.params.placements)},
            )
            _persist_trace(trace, resolved_trace_path)

        mesh_started = time.perf_counter()
        mesh_started_at = utc_now()
        active_stage = "geometry_meshes"
        try:
            enclosure = build_enclosure(enclosure_plan)
        except GeometryEngineError as exc:
            issues.append(Issue(code="GEOMETRY_FAILED", severity="error", message=str(exc)))
            if trace is not None:
                trace.record_stage(
                    "geometry_meshes",
                    "failed",
                    started_at=mesh_started_at,
                    finished_at=utc_now(),
                    duration_ms=(time.perf_counter() - mesh_started) * 1000.0,
                    issues=[Issue(code="GEOMETRY_FAILED", severity="error", message=str(exc))],
                )
                reason = "not reached because mesh generation failed"
                trace.record_stage(
                    "slicing",
                    "skipped",
                    details={"requested": do_slice},
                    reason=reason,
                )
                complete_trace_stages(trace, reason=reason)
                _persist_trace(trace, resolved_trace_path)
            active_stage = None
            result = PipelineResult(
                status="failed",
                idea=idea,
                plan=plan,
                spec=spec,
                enclosure_plan=enclosure_plan,
                issues=issues,
                run_id=run_id,
                trace_path=resolved_trace_path,
            )
            return result

        active_stage = None
        if trace is not None:
            trace.record_stage(
                "geometry_meshes",
                "passed",
                started_at=mesh_started_at,
                finished_at=utc_now(),
                duration_ms=(time.perf_counter() - mesh_started) * 1000.0,
                details={"engine": _geometry_engine_name()},
            )
            trace.record_stage(
                "geometry_stl_export",
                "skipped" if out_path is None else "passed",
                reason="out_dir was not provided" if out_path is None else None,
                artifacts={} if out_path is None else {},
            )
            _persist_trace(trace, resolved_trace_path)

        # Firmware.
        firmware_started = time.perf_counter()
        firmware_started_at = utc_now()
        active_stage = "firmware_generation"
        try:
            firmware = generate_firmware(spec, catalog)
        except FirmwareError as exc:
            issues.append(Issue(code="FIRMWARE_FAILED", severity="error", message=str(exc)))
            if trace is not None:
                trace.record_stage(
                    "firmware_generation",
                    "failed",
                    started_at=firmware_started_at,
                    finished_at=utc_now(),
                    duration_ms=(time.perf_counter() - firmware_started) * 1000.0,
                    issues=[Issue(code="FIRMWARE_FAILED", severity="error", message=str(exc))],
                )
                reason = "not reached because firmware generation failed"
                trace.record_stage(
                    "slicing",
                    "skipped",
                    details={"requested": do_slice},
                    reason=reason,
                )
                complete_trace_stages(trace, reason=reason)
                _persist_trace(trace, resolved_trace_path)
            active_stage = None
            result = PipelineResult(
                status="failed",
                idea=idea,
                plan=plan,
                spec=spec,
                enclosure_plan=enclosure_plan,
                enclosure=enclosure,
                issues=issues,
                run_id=run_id,
                trace_path=resolved_trace_path,
            )
            return result

        active_stage = None
        if trace is not None:
            trace.record_stage(
                "firmware_generation",
                "passed",
                started_at=firmware_started_at,
                finished_at=utc_now(),
                duration_ms=(time.perf_counter() - firmware_started) * 1000.0,
                issues=list(firmware.issues),
                details={"libraries": list(firmware.libraries)},
            )
            _persist_trace(trace, resolved_trace_path)

        previous_firmware_issues = list(firmware.issues)
        if do_compile:
            compile_started = time.perf_counter()
            compile_started_at = utc_now()
            active_stage = "firmware_compile"
            firmware = compile_firmware(firmware, Path(out_path or ".") / "sparkplan_ino")
            active_stage = None
            compile_issues = [
                issue for issue in firmware.issues if issue not in previous_firmware_issues
            ]
            compile_status = {
                "ok": "passed",
                "skipped": "skipped",
            }.get(firmware.compile_status, "failed")
            if trace is not None:
                trace.record_stage(
                    "firmware_compile",
                    compile_status,
                    started_at=compile_started_at,
                    finished_at=utc_now(),
                    duration_ms=(time.perf_counter() - compile_started) * 1000.0,
                    issues=compile_issues,
                    details={
                        "compile_status": firmware.compile_status,
                        "arduino_cli": _arduino_cli_version(),
                    },
                    reason=(
                        None
                        if compile_status != "skipped"
                        else "arduino-cli or a dependency is unavailable"
                    ),
                )
                _persist_trace(trace, resolved_trace_path)
        else:
            if trace is not None:
                trace.record_stage(
                    "firmware_compile",
                    "skipped",
                    reason="do_compile is False",
                )
                _persist_trace(trace, resolved_trace_path)

        active_stage = "guide"
        guide = render_guide(spec, catalog, enclosure_plan, firmware, issues)
        active_stage = None
        if trace is not None:
            trace.record_stage(
                "guide",
                "passed",
                details={"characters": len(guide)},
            )
            _persist_trace(trace, resolved_trace_path)

        if out_path is not None:
            write_started = time.perf_counter()
            write_started_at = utc_now()
            try:
                out_path.mkdir(parents=True, exist_ok=True)
                enclosure.export_base_stl(out_path / "enclosure_base.stl")
                enclosure.export_lid_stl(out_path / "enclosure_lid.stl")
                (out_path / "sparkplan_ino").mkdir(parents=True, exist_ok=True)
                (out_path / "sparkplan_ino" / "sparkplan.ino").write_text(firmware.sketch)
                (out_path / "BUILD_GUIDE.md").write_text(guide)
                (out_path / "buildspec.json").write_text(spec.model_dump_json(indent=2))
            except Exception as exc:
                if trace is not None:
                    trace.record_stage(
                        "geometry_stl_export",
                        "failed",
                        started_at=write_started_at,
                        finished_at=utc_now(),
                        duration_ms=(time.perf_counter() - write_started) * 1000.0,
                        issues=[
                            Issue(
                                code="ARTIFACT_WRITE_FAILED",
                                severity="error",
                                message=str(exc),
                            )
                        ],
                    )
                    _persist_trace(trace, resolved_trace_path)
                raise
            if trace is not None:
                trace.record_stage(
                    "geometry_stl_export",
                    "passed",
                    started_at=write_started_at,
                    finished_at=utc_now(),
                    duration_ms=(time.perf_counter() - write_started) * 1000.0,
                    artifacts={
                        "base_stl": "enclosure_base.stl",
                        "lid_stl": "enclosure_lid.stl",
                        "firmware_sketch": "sparkplan_ino/sparkplan.ino",
                        "guide": "BUILD_GUIDE.md",
                        "buildspec": "buildspec.json",
                    },
                )
                if resolved_trace_path is not None:
                    trace.artifacts["trace"] = resolved_trace_path.name
                _persist_trace(trace, resolved_trace_path)

        active_stage = "slicing"
        if do_slice and out_path is None:
            slice_issue = Issue(
                code="SLICE_OUTPUT_DIR_REQUIRED",
                severity="error",
                message="Slicing was requested but no output directory was provided.",
                fix_hint="Pass out_dir so enclosure_base.stl and enclosure_lid.stl exist.",
            )
            slice_result: SliceResult | None = SliceResult(
                status="failed",
                reason=slice_issue.message,
                issues=[slice_issue],
            )
        else:
            root = out_path if out_path is not None else Path(".")
            slice_result = verify_slice(
                root / "enclosure_base.stl",
                root / "enclosure_lid.stl",
                output_dir=out_path,
                enabled=do_slice,
            )
        active_stage = None

        if trace is not None:
            if out_path is None:
                trace.record_stage(
                    "geometry_stl_export",
                    "skipped",
                    reason="out_dir was not provided",
                )
            trace.record_stage(
                "slicing",
                slice_result.status,
                duration_ms=slice_result.duration_ms,
                issues=list(slice_result.issues),
                details=slice_details(slice_result, requested=do_slice),
                artifacts=_slice_artifacts(slice_result, out_path),
                reason=slice_result.reason,
            )
            trace.record_stage(
                "simulation",
                "unverified",
                reason="Wokwi or hardware simulation was not performed",
            )
            _persist_trace(trace, resolved_trace_path)

        result = PipelineResult(
            status="ok",
            idea=idea,
            plan=plan,
            spec=spec,
            enclosure_plan=enclosure_plan,
            enclosure=enclosure,
            firmware=firmware,
            guide=guide,
            slice_result=slice_result,
            issues=issues,
            run_id=run_id,
            trace_path=resolved_trace_path,
        )
        return result
    except BaseException as exc:
        exc_info = exc
        if trace is not None:
            if active_stage is not None and active_stage not in trace.stages:
                trace.record_stage(
                    active_stage,
                    "failed",
                    finished_at=utc_now(),
                    issues=[],
                    details={"error": f"{type(exc).__name__}: {exc}"},
                    reason=f"unexpected {type(exc).__name__} while running this stage",
                )
            complete_trace_stages(
                trace,
                reason="not reached because an earlier stage raised an unexpected exception",
            )
            trace.status = "failed"
            trace.finished_at = utc_now()
            _persist_trace(trace, resolved_trace_path)
        raise
    finally:
        if trace is not None and exc_info is None and result is not None:
            complete_trace_stages(trace, reason="not reached before the run finished")
            failed_stages = [stage for stage in trace.stages.values() if stage.status == "failed"]
            slice_stage = trace.stages.get("slicing")
            requested_slice_unverified = bool(
                slice_stage is not None
                and slice_stage.details.get("requested") is True
                and slice_stage.status != "passed"
            )
            trace.status = (
                "failed"
                if result.status != "ok" or failed_stages or requested_slice_unverified
                else "passed"
            )
            trace.finished_at = utc_now()
            _persist_trace(trace, resolved_trace_path)
        if trace is not None and exc_info is not None:
            trace.status = "failed"
            trace.finished_at = utc_now()
            _persist_trace(trace, resolved_trace_path)


def _has_errors(issues: list[Issue]) -> bool:
    return any(issue.severity == "error" for issue in issues)


def _slice_artifacts(result: SliceResult, out_path: Path | None) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for name, part in result.parts.items():
        if part.output_path is None:
            continue
        path = Path(part.output_path)
        if out_path is not None:
            with contextlib.suppress(ValueError):
                path = path.relative_to(out_path)
        artifacts[f"{name}_gcode"] = str(path)
    return artifacts


def _geometry_engine_name() -> str | None:
    from sparkplan.geometry.engine import available_engines

    engines = available_engines()
    if "cadquery" in engines:
        return "cadquery"
    if "build123d" in engines:
        return "build123d"
    return None


def _arduino_cli_version() -> str | None:
    import os
    import shutil
    import subprocess

    cli = os.environ.get("SPARKPLAN_ARDUINO_CLI") or shutil.which("arduino-cli")
    if not cli:
        return None
    try:
        completed = subprocess.run(
            [cli, "version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (completed.stdout or completed.stderr).strip()
    return output.splitlines()[0] if output else None


def _persist_trace(trace: RunTrace | None, path: Path | None) -> None:
    if trace is None or path is None:
        return
    try:
        write_trace(trace, path)
    except Exception as exc:
        # Remove an empty claim file, but preserve a previously written trace.
        with contextlib.suppress(OSError):
            if path.exists() and path.stat().st_size == 0:
                path.unlink()
        # Trace writing is additive: never mask the pipeline result or an
        # original exception.
        trace.trace_write_error = str(exc)
