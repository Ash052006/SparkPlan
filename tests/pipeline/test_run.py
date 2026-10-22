"""Canonical Phase 6 run tests.

All LLM calls are scripted and both external tools are mocked unless a test is
specifically checking missing-tool truthfulness. No test touches the network.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from sparkplan.firmware import FirmwareResult
from sparkplan.geometry.enclosure import EnclosureLayout
from sparkplan.geometry.generate import GeometryResult
from sparkplan.geometry.layout import pack_enclosure
from sparkplan.geometry.slicing import SlicePartResult, SliceResult
from sparkplan.llm.phase3 import FakeLLM
from sparkplan.models.catalog import Catalog, get_catalog
from sparkplan.models.spec import Issue
from sparkplan.pipeline.run_models import RunStatus, StageStatus
from sparkplan.pipeline.run_paths import InvalidRunId, RunExistsError, RunPaths
from tests.planner.phase3.conftest import unsupported_spec, valid_spec

run_module = importlib.import_module("sparkplan.pipeline.run")


def issue(
    code: str,
    message: str,
    *,
    severity: str = "error",
) -> Issue:
    return Issue(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        message=message,
        part_ids=[],
        fix_hint="test",
    )


def fake_geometry(
    catalog: Catalog,
    *,
    layout: EnclosureLayout | None = None,
    write_stls: bool = True,
    with_report: bool = False,
    issues: list[Issue] | None = None,
) -> Any:
    def generate(spec, cat=None, config=None, out_dir=None):
        actual_layout = layout if layout is not None else pack_enclosure(spec, cat or catalog)[0]
        result = GeometryResult(layout=actual_layout, issues=list(issues or []), engine="test")
        if write_stls and actual_layout is not None and out_dir is not None:
            out = Path(out_dir)
            result.exported = []
            for name in ("base", "lid", "assembled"):
                path = out / f"{name}.stl"
                path.write_bytes(b"solid test\nendsolid test\n")
                result.exported.append(path)
            if with_report:
                report = out / "geometry_report.json"
                report.write_text('{"ok": true}', encoding="utf-8")
                result.report_path = report
        return result

    return generate


def fake_slice(*, with_outputs: bool = True, status: str = "passed") -> Any:
    def verify(base_stl, lid_stl, *, output_dir=None, **kwargs):
        base = Path(base_stl)
        lid = Path(lid_stl)
        out = Path(output_dir) if output_dir is not None else base.parent
        parts: dict[str, SlicePartResult] = {}
        for name, path in (("base", base), ("lid", lid)):
            output = out / f"{path.stem}.gcode"
            if with_outputs and status == "passed":
                output.write_text("; test gcode\nG1 X0 Y0\n", encoding="utf-8")
            parts[name] = SlicePartResult(
                name=name,
                status=status,  # type: ignore[arg-type]
                input_path=str(path),
                output_path=str(output),
                exit_code=0 if status == "passed" else 1,
                duration_ms=1.0,
                reason=None if status == "passed" else "slicer failed",
            )
        return SliceResult(
            status=status,  # type: ignore[arg-type]
            slicer="/fake/prusa-slicer",
            version="2.8.0",
            profile="/fake/profile.ini",
            parts=parts,
            issues=[] if status == "passed" else [issue("SLICE_PROCESS_FAILED", "slicer failed")],
            reason=None if status == "passed" else "slicer failed",
        )

    return verify


def fake_compile(*, status: str = "ok", errors: list[Issue] | None = None) -> Any:
    calls: list[dict[str, Any]] = []

    def compile_result(
        result: FirmwareResult,
        directory: str | Path,
        cli_path: str | None = None,
        extra_lib_install: bool = True,
        *,
        sketch_name: str | None = None,
        write_sketch_file: bool = True,
    ) -> FirmwareResult:
        calls.append(
            {
                "directory": Path(directory),
                "sketch_name": sketch_name,
                "write_sketch_file": write_sketch_file,
            }
        )
        return FirmwareResult(
            sketch=result.sketch,
            libraries=result.libraries,
            compile_status=status,  # type: ignore[arg-type]
            compile_log=f"compiled {Path(directory) / (sketch_name or 'sparkplan.ino')}",
            issues=list(errors or []),
        )

    compile_result.calls = calls  # type: ignore[attr-defined]
    return compile_result


def run_once(
    tmp_path: Path,
    catalog: Catalog,
    *,
    idea: str = "a desk plant monitor",
    script: list[Any] | None = None,
    **kwargs: Any,
):
    return run_module.run(
        idea,
        FakeLLM(script=script or [valid_spec()]),
        run_id="test_run",
        runs_root=tmp_path / "runs",
        catalog=catalog,
        **kwargs,
    )


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return get_catalog()


def test_success_persists_required_artifacts_and_ordered_stages(
    tmp_path: Path, catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    geometry_note = issue(
        "GEOMETRY_NOTE",
        f"layout written under {tmp_path / 'runs' / 'test_run'}",
        severity="warning",
    )
    monkeypatch.setattr(
        run_module, "generate_enclosure", fake_geometry(catalog, issues=[geometry_note])
    )
    monkeypatch.setattr(run_module, "verify_slice", fake_slice())
    compile_result = fake_compile()
    monkeypatch.setattr(run_module, "compile_firmware", compile_result)

    result = run_once(tmp_path, catalog)
    runs = tmp_path / "runs" / "test_run"

    assert result.status is RunStatus.OK
    assert [stage.name for stage in result.stages] == list(run_module.STAGE_ORDER)
    assert [stage.status for stage in result.stages] == [StageStatus.PASSED] * 7
    assert {
        "spec",
        "base_stl",
        "lid_stl",
        "assembled_stl",
        "sketch",
        "guide",
        "report",
        "trace",
    }.issubset(result.artifacts)
    for name, relative in result.artifacts.items():
        assert (runs / relative).is_file(), name
        assert (runs / relative).stat().st_size > 0

    spec = json.loads((runs / "spec.json").read_text())
    assert spec["title"] == "Soil moisture monitor"
    report = json.loads((runs / "report.json").read_text())
    trace = json.loads((runs / "trace.json").read_text())
    assert report["status"] == "ok"
    assert report["artifacts"] == result.artifacts
    assert trace["status"] == "ok"
    assert trace["stage_order"] == list(run_module.STAGE_ORDER)
    assert [stage["name"] for stage in trace["stages"]] == list(run_module.STAGE_ORDER)
    assert trace["validation_report"]["passed"] is True

    assert compile_result.calls == [
        {
            "directory": runs / "sketch",
            "sketch_name": "sketch.ino",
            "write_sketch_file": False,
        }
    ]
    guide = (runs / "GUIDE.md").read_text()
    assert "Soil moisture monitor" in guide
    assert "## 10. Verification" in guide
    assert "| Validated | true |" in guide
    assert "| Compiled | true |" in guide
    assert "| Sliced | true |" in guide
    assert "| Simulated | skipped |" in guide
    assert guide.endswith("Not physically built.")
    assert trace["verification"] == {
        "validated": True,
        "compiled": True,
        "sliced": True,
        "simulated": "skipped",
    }
    assert (runs / "sketch" / "sketch.ino").read_text().strip()
    assert str(tmp_path) not in (runs / "report.json").read_text()
    assert str(tmp_path) not in (runs / "trace.json").read_text()


def test_geometry_report_does_not_trigger_missing_stl_failure(
    tmp_path: Path, catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        run_module,
        "generate_enclosure",
        fake_geometry(catalog, with_report=True),
    )
    monkeypatch.setattr(run_module, "verify_slice", fake_slice())
    monkeypatch.setattr(run_module, "compile_firmware", fake_compile())

    result = run_once(tmp_path, catalog)

    assert result.status is RunStatus.OK
    geometry = result.stage("geometry")
    assert geometry is not None
    assert geometry.status is StageStatus.PASSED
    assert geometry.artifacts["geometry_report"] == "geometry_report.json"
    assert {"base_stl", "lid_stl", "assembled_stl"}.issubset(geometry.artifacts)


def test_refusal_creates_no_run_directory(tmp_path: Path, catalog: Catalog) -> None:
    result = run_module.run(
        "",
        FakeLLM(script=[]),
        run_id="refused_run",
        runs_root=tmp_path / "runs",
        catalog=catalog,
    )

    assert result.status is RunStatus.REFUSED
    assert result.stages[0].name == "plan"
    assert result.stages[0].status is StageStatus.FAILED
    assert [stage.name for stage in result.stages] == list(run_module.STAGE_ORDER)
    assert not (tmp_path / "runs").exists()
    assert result.artifacts == {}


def test_unsupported_idea_creates_no_run_directory(tmp_path: Path, catalog: Catalog) -> None:
    result = run_module.run(
        "an autonomous drone",
        FakeLLM(script=[unsupported_spec()]),
        run_id="unsupported_run",
        runs_root=tmp_path / "runs",
        catalog=catalog,
    )

    assert result.status is RunStatus.UNSUPPORTED
    assert result.stages[0].status is StageStatus.FAILED
    assert result.stages[1].status is StageStatus.SKIPPED
    assert not (tmp_path / "runs").exists()
    assert result.artifacts == {}


def test_planning_failure_creates_no_run_directory(tmp_path: Path, catalog: Catalog) -> None:
    bad = valid_spec()
    bad["parts"][1]["part_id"] = "not_in_catalog"

    result = run_module.run(
        "a desk plant monitor",
        FakeLLM(script=[bad] * 10),
        run_id="failed_plan",
        runs_root=tmp_path / "runs",
        catalog=catalog,
    )

    assert result.status is RunStatus.FAILED
    assert result.stage("plan").status is StageStatus.FAILED  # type: ignore[union-attr]
    assert result.stage("validation").status is StageStatus.FAILED  # type: ignore[union-attr]
    assert not (tmp_path / "runs").exists()
    assert result.artifacts == {}


def test_planner_exception_is_reported_without_artifacts(
    tmp_path: Path, catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(self, idea: str):
        raise RuntimeError("planner exploded")

    monkeypatch.setattr(run_module.Planner, "plan", explode)
    result = run_module.run(
        "a desk plant monitor",
        FakeLLM(script=[valid_spec()]),
        run_id="planner_exception",
        runs_root=tmp_path / "runs",
        catalog=catalog,
    )

    assert result.status is RunStatus.FAILED
    assert result.stage("plan").status is StageStatus.FAILED  # type: ignore[union-attr]
    assert any(i.code == "PLAN_EXCEPTION" for i in result.issues)
    assert not (tmp_path / "runs").exists()


def test_existing_run_is_not_overwritten(tmp_path: Path, catalog: Catalog) -> None:
    paths = RunPaths("taken", tmp_path / "runs")
    paths.claim()
    marker = paths.run_dir / "marker.txt"
    marker.write_text("keep me", encoding="utf-8")
    llm = FakeLLM(script=[valid_spec()])

    with pytest.raises(RunExistsError):
        run_module.run(
            "a desk plant monitor",
            llm,
            run_id="taken",
            runs_root=tmp_path / "runs",
            catalog=catalog,
        )

    assert llm.calls == []
    assert marker.read_text(encoding="utf-8") == "keep me"


def test_invalid_run_id_is_rejected(tmp_path: Path, catalog: Catalog) -> None:
    with pytest.raises(InvalidRunId):
        run_module.run(
            "a desk plant monitor",
            FakeLLM(script=[valid_spec()]),
            run_id="../escape",
            runs_root=tmp_path / "runs",
            catalog=catalog,
        )


def test_geometry_failure_still_runs_firmware_and_guide(
    tmp_path: Path, catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    geometry_issue = issue("GEOMETRY_BUILD_FAILED", "could not build the shell")
    monkeypatch.setattr(
        run_module,
        "generate_enclosure",
        fake_geometry(catalog, layout=None, write_stls=False, issues=[geometry_issue]),
    )
    compile_result = fake_compile()
    monkeypatch.setattr(run_module, "compile_firmware", compile_result)

    result = run_once(tmp_path, catalog)

    assert result.status is RunStatus.FAILED
    assert result.stage("geometry").status is StageStatus.FAILED  # type: ignore[union-attr]
    assert result.stage("slicing").status is StageStatus.SKIPPED  # type: ignore[union-attr]
    assert result.stage("firmware_generation").status is StageStatus.PASSED  # type: ignore[union-attr]
    assert result.stage("firmware_compile").status is StageStatus.PASSED  # type: ignore[union-attr]
    assert result.stage("guide").status is StageStatus.PASSED  # type: ignore[union-attr]
    assert "base_stl" not in result.artifacts
    assert "sketch" in result.artifacts
    assert "guide" in result.artifacts
    guide = (tmp_path / "runs" / "test_run" / "GUIDE.md").read_text()
    assert "No enclosure layout was generated" in guide


def test_unavailable_geometry_engine_is_unverified_not_passed(
    tmp_path: Path, catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    warning = issue(
        "GEOMETRY_ENGINE_UNAVAILABLE",
        "no solid-modelling engine",
        severity="warning",
    )
    monkeypatch.setattr(
        run_module,
        "generate_enclosure",
        fake_geometry(catalog, write_stls=False, issues=[warning]),
    )
    monkeypatch.setattr(run_module, "compile_firmware", fake_compile())

    result = run_once(tmp_path, catalog)

    assert result.status is RunStatus.OK
    assert result.stage("geometry").status is StageStatus.UNVERIFIED  # type: ignore[union-attr]
    assert result.stage("slicing").status is StageStatus.SKIPPED  # type: ignore[union-attr]
    assert "base_stl" not in result.artifacts
    assert result.stage("guide").status is StageStatus.PASSED  # type: ignore[union-attr]


def test_success_without_expected_stls_is_failed(
    tmp_path: Path, catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        run_module,
        "generate_enclosure",
        fake_geometry(catalog, write_stls=False),
    )
    monkeypatch.setattr(run_module, "compile_firmware", fake_compile())

    result = run_once(tmp_path, catalog)

    assert result.status is RunStatus.FAILED
    assert result.stage("geometry").status is StageStatus.FAILED  # type: ignore[union-attr]
    assert any(i.code == "GEOMETRY_OUTPUT_MISSING" for i in result.issues)
    assert result.stage("slicing").status is StageStatus.SKIPPED  # type: ignore[union-attr]


def test_missing_slicer_is_unverified_and_does_not_fail_the_run(
    tmp_path: Path, catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_module, "generate_enclosure", fake_geometry(catalog))
    monkeypatch.setattr(run_module, "compile_firmware", fake_compile())

    result = run_once(
        tmp_path,
        catalog,
        slicer=tmp_path / "missing-prusa-slicer",
    )

    stage = result.stage("slicing")
    assert stage is not None
    assert stage.status is StageStatus.UNVERIFIED
    assert stage.reason
    assert result.status is RunStatus.OK
    assert "base_gcode" not in result.artifacts


def test_slicer_reported_success_without_output_is_failed(
    tmp_path: Path, catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_module, "generate_enclosure", fake_geometry(catalog))
    monkeypatch.setattr(run_module, "verify_slice", fake_slice(with_outputs=False))
    monkeypatch.setattr(run_module, "compile_firmware", fake_compile())

    result = run_once(tmp_path, catalog)

    assert result.status is RunStatus.FAILED
    assert result.stage("slicing").status is StageStatus.FAILED  # type: ignore[union-attr]
    assert any(i.code == "SLICE_OUTPUT_MISSING" for i in result.issues)


def test_missing_compiler_is_unverified_and_does_not_fail_the_run(
    tmp_path: Path, catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_module, "generate_enclosure", fake_geometry(catalog))
    monkeypatch.setattr(run_module, "verify_slice", fake_slice())
    monkeypatch.setattr(
        run_module,
        "compile_firmware",
        fake_compile(
            status="skipped",
            errors=[issue("ARDUINO_CLI_MISSING", "arduino-cli is not installed")],
        ),
    )

    result = run_once(tmp_path, catalog)

    stage = result.stage("firmware_compile")
    assert stage is not None
    assert stage.status is StageStatus.UNVERIFIED
    assert stage.reason
    assert result.status is RunStatus.OK
    guide = (tmp_path / "runs" / "test_run" / "GUIDE.md").read_text()
    trace = json.loads((tmp_path / "runs" / "test_run" / "trace.json").read_text())
    assert "| Compiled | skipped |" in guide
    assert trace["verification"]["compiled"] == "skipped"


def test_real_compile_failure_fails_the_run_but_guide_is_written(
    tmp_path: Path, catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_module, "generate_enclosure", fake_geometry(catalog))
    monkeypatch.setattr(run_module, "verify_slice", fake_slice())
    monkeypatch.setattr(
        run_module,
        "compile_firmware",
        fake_compile(status="failed", errors=[issue("COMPILE_ERROR", "compiler failed")]),
    )

    result = run_once(tmp_path, catalog)

    assert result.status is RunStatus.FAILED
    assert result.stage("firmware_compile").status is StageStatus.FAILED  # type: ignore[union-attr]
    assert result.stage("guide").status is StageStatus.PASSED  # type: ignore[union-attr]
    assert (tmp_path / "runs" / "test_run" / "GUIDE.md").is_file()


def test_slicing_and_compile_can_be_disabled(
    tmp_path: Path, catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(run_module, "generate_enclosure", fake_geometry(catalog))
    monkeypatch.setattr(
        run_module,
        "verify_slice",
        lambda *args, **kwargs: seen.append("slice"),
    )
    monkeypatch.setattr(
        run_module,
        "compile_firmware",
        lambda *args, **kwargs: seen.append("compile"),
    )

    result = run_once(tmp_path, catalog, do_slice=False, do_compile=False)

    assert seen == []
    assert result.status is RunStatus.OK
    assert result.stage("slicing").status is StageStatus.SKIPPED  # type: ignore[union-attr]
    assert result.stage("firmware_compile").status is StageStatus.SKIPPED  # type: ignore[union-attr]
    assert result.stage("guide").status is StageStatus.PASSED  # type: ignore[union-attr]


def test_planning_failure_after_geometry_repair_has_no_artifacts(
    tmp_path: Path, catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sparkplan.planner.phase3.planner as planner_module

    def failing_enclosure(spec, cat=None, config=None, out_dir=None):
        return GeometryResult(
            layout=None,
            issues=[issue("GEOMETRY_BUILD_FAILED", "invalid enclosure layout")],
        )

    monkeypatch.setattr(planner_module, "generate_enclosure", failing_enclosure)
    result = run_module.run(
        "a desk plant monitor",
        FakeLLM(script=[valid_spec()]),
        run_id="geometry_plan_failure",
        runs_root=tmp_path / "runs",
        catalog=catalog,
    )

    assert result.status is RunStatus.FAILED
    assert result.stage("plan").status is StageStatus.FAILED  # type: ignore[union-attr]
    assert result.stage("validation").status is StageStatus.PASSED  # type: ignore[union-attr]
    assert not (tmp_path / "runs").exists()
    assert result.artifacts == {}


def test_planner_ok_without_a_spec_is_failed_not_ok(
    tmp_path: Path, catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sparkplan.planner.phase3.models import PlanResult, PlanStatus

    monkeypatch.setattr(
        run_module.Planner,
        "plan",
        lambda self, idea: PlanResult(status=PlanStatus.OK, idea=idea),
    )
    result = run_module.run(
        "a desk plant monitor",
        FakeLLM(script=[valid_spec()]),
        run_id="missing_plan_spec",
        runs_root=tmp_path / "runs",
        catalog=catalog,
    )

    assert result.status is RunStatus.FAILED
    assert result.stage("plan").status is StageStatus.FAILED  # type: ignore[union-attr]
    assert any(i.code == "PLAN_SPEC_MISSING" for i in result.issues)
    assert not (tmp_path / "runs").exists()


def test_geometry_error_gets_one_repair_and_is_recorded_in_the_trace(
    tmp_path: Path, catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sparkplan.planner.phase3.planner as planner_module

    collision = issue("GEOMETRY_PART_COLLISION", "board and screen collide.")
    geometry_calls = {"count": 0}

    def planner_geometry(spec, cat=None, config=None, out_dir=None):
        geometry_calls["count"] += 1
        if geometry_calls["count"] == 1:
            return GeometryResult(layout=None, issues=[collision])
        return GeometryResult(
            layout=pack_enclosure(spec, cat or catalog)[0],
            issues=[],
            engine="test",
        )

    monkeypatch.setattr(planner_module, "generate_enclosure", planner_geometry)
    monkeypatch.setattr(run_module, "generate_enclosure", fake_geometry(catalog))
    monkeypatch.setattr(run_module, "verify_slice", fake_slice())
    monkeypatch.setattr(run_module, "compile_firmware", fake_compile())

    result = run_module.run(
        "a desk plant monitor",
        FakeLLM(script=[valid_spec(), valid_spec()]),
        run_id="geometry_repair",
        runs_root=tmp_path / "runs",
        catalog=catalog,
    )

    trace = json.loads((tmp_path / "runs" / "geometry_repair" / "trace.json").read_text())
    plan_details = trace["stages"][0]["details"]
    attempts = plan_details["attempts"]

    assert result.status is RunStatus.OK
    assert geometry_calls["count"] == 2
    assert plan_details["llm_calls"] == 2
    assert plan_details["repair_rounds"] == 1
    assert [attempt["kind"] for attempt in attempts] == ["geometry_error", "ok"]
    assert trace["verification"]["validated"] is True
