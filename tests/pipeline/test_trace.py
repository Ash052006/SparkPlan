from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sparkplan.geometry import SlicePartResult, SliceResult, available_engines
from sparkplan.llm import FakeLLM
from sparkplan.models.spec import Issue
from sparkplan.pipeline import run_pipeline

EXPECTED_STAGES = {
    "plan",
    "validation",
    "geometry_layout",
    "geometry_meshes",
    "geometry_stl_export",
    "firmware_generation",
    "firmware_compile",
    "guide",
    "slicing",
    "simulation",
}

VALID_SPEC = {
    "idea": "desk weather station",
    "controller": "esp32_devkit_v4",
    "parts": [
        {"instance_id": "env", "part_id": "dht22"},
        {"instance_id": "screen", "part_id": "ssd1306_oled"},
    ],
    "connections": [
        {"instance_id": "env", "peripheral_pin": "DATA", "controller_pin": "GPIO4"},
        {"instance_id": "screen", "peripheral_pin": "SDA", "controller_pin": "GPIO21"},
        {"instance_id": "screen", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
    ],
    "actions": [
        {
            "action_id": "read_temp",
            "kind": "read",
            "instance_id": "env",
            "api": "read_temperature_c",
        },
        {
            "action_id": "show",
            "kind": "write",
            "instance_id": "screen",
            "api": "print_line1",
            "consumes": "read_temp",
        },
    ],
}


@pytest.mark.skipif(not available_engines(), reason="no geometry engine")
def test_positional_trace_path_is_not_interpreted_as_do_slice(tmp_path):
    explicit_trace = tmp_path / "explicit-trace.json"
    result = run_pipeline(
        "desk weather station",
        FakeLLM(script=[VALID_SPEC]),
        None,
        tmp_path,
        False,
        explicit_trace,
    )

    assert result.status == "ok"
    assert result.trace_path == explicit_trace
    assert explicit_trace.is_file()
    assert result.slice_result is not None
    assert result.slice_result.status == "skipped"


@pytest.mark.skipif(not available_engines(), reason="no geometry engine")
def test_trace_written_for_successful_run(tmp_path):
    result = run_pipeline("desk weather station", FakeLLM(script=[VALID_SPEC]), out_dir=tmp_path)
    assert result.status == "ok"
    assert result.trace_path == tmp_path / "trace.json"
    assert result.trace_path.is_file()

    trace = json.loads(result.trace_path.read_text())
    assert trace["schema_version"] == "1.0"
    assert trace["run_id"] == result.run_id
    assert trace["status"] == "passed"
    assert set(trace["stages"]) == EXPECTED_STAGES
    assert trace["stages"]["plan"]["status"] == "passed"
    assert trace["stages"]["validation"]["status"] == "passed"
    assert trace["stages"]["geometry_layout"]["status"] == "passed"
    assert trace["stages"]["geometry_meshes"]["status"] == "passed"
    assert trace["stages"]["geometry_stl_export"]["status"] == "passed"
    assert trace["stages"]["firmware_generation"]["status"] == "passed"
    assert trace["stages"]["firmware_compile"]["status"] == "skipped"
    assert trace["stages"]["guide"]["status"] == "passed"
    assert trace["stages"]["slicing"]["status"] == "skipped"
    assert trace["stages"]["slicing"]["details"]["requested"] is False
    assert trace["stages"]["simulation"]["status"] == "unverified"
    assert trace["stages"]["slicing"]["reason"] == "slicing was not requested"
    assert (
        trace["stages"]["simulation"]["reason"] == "Wokwi or hardware simulation was not performed"
    )


def _slice_result(
    base_stl: Path,
    lid_stl: Path,
    output_dir: Path | None,
    *,
    status: str,
    base_status: str | None = None,
    lid_status: str | None = None,
) -> SliceResult:
    output_dir = output_dir or base_stl.parent
    parts: dict[str, SlicePartResult] = {}
    issues: list[Issue] = []
    for name, source, part_status in (
        ("base", base_stl, base_status or status),
        ("lid", lid_stl, lid_status or status),
    ):
        output = output_dir / f"{source.stem}.gcode"
        output.write_text("; gcode\n")
        part_issues: list[Issue] = []
        if part_status != "passed":
            issue = Issue(
                code="SLICE_PROCESS_FAILED",
                severity="error",
                message=f"{name} failed",
            )
            part_issues.append(issue)
            issues.append(issue)
        parts[name] = SlicePartResult(
            name=name,
            status=part_status,  # type: ignore[arg-type]
            input_path=str(source),
            output_path=str(output),
            exit_code=0 if part_status == "passed" else 1,
            duration_ms=1.0,
            diagnostics="",
            reason=None if part_status == "passed" else f"{name} failed",
            issues=part_issues,
        )
    return SliceResult(
        status=status,  # type: ignore[arg-type]
        slicer="/usr/bin/prusa-slicer",
        version="2.7.2",
        profile="sparkplan/geometry/prusaslicer.ini",
        parts=parts,
        issues=issues,
        duration_ms=2.0,
        reason=None if status == "passed" else "slicing failed",
    )


@pytest.mark.skipif(not available_engines(), reason="no geometry engine")
def test_trace_slicing_passed_only_when_both_parts_pass(tmp_path, monkeypatch):
    import sparkplan.pipeline.pipeline as pipeline

    def fake_verify(base_stl, lid_stl, *, output_dir=None, enabled=True, **kwargs):
        assert enabled is True
        return _slice_result(Path(base_stl), Path(lid_stl), output_dir, status="passed")

    monkeypatch.setattr(pipeline, "verify_slice", fake_verify)
    result = run_pipeline(
        "desk weather station",
        FakeLLM(script=[VALID_SPEC]),
        out_dir=tmp_path,
        do_slice=True,
    )

    assert result.status == "ok"
    assert result.slice_result is not None
    assert result.slice_result.status == "passed"
    trace = json.loads((tmp_path / "trace.json").read_text())
    assert trace["status"] == "passed"
    assert trace["stages"]["slicing"]["status"] == "passed"
    assert trace["stages"]["slicing"]["details"]["slicer"] == "/usr/bin/prusa-slicer"
    assert trace["stages"]["slicing"]["details"]["version"] == "2.7.2"
    assert trace["stages"]["slicing"]["details"]["parts"]["base"]["status"] == "passed"
    assert trace["stages"]["slicing"]["details"]["parts"]["lid"]["status"] == "passed"
    assert trace["stages"]["slicing"]["artifacts"]["base_gcode"] == "enclosure_base.gcode"
    assert trace["stages"]["slicing"]["artifacts"]["lid_gcode"] == "enclosure_lid.gcode"


@pytest.mark.skipif(not available_engines(), reason="no geometry engine")
def test_trace_slicing_failure_reports_parts_and_fails_run(tmp_path, monkeypatch):
    import sparkplan.pipeline.pipeline as pipeline

    def fake_verify(base_stl, lid_stl, *, output_dir=None, enabled=True, **kwargs):
        return _slice_result(
            Path(base_stl),
            Path(lid_stl),
            output_dir,
            status="failed",
            base_status="failed",
            lid_status="passed",
        )

    monkeypatch.setattr(pipeline, "verify_slice", fake_verify)
    result = run_pipeline(
        "desk weather station",
        FakeLLM(script=[VALID_SPEC]),
        out_dir=tmp_path,
        do_slice=True,
    )

    assert result.status == "ok"
    trace = json.loads((tmp_path / "trace.json").read_text())
    assert trace["status"] == "failed"
    assert trace["stages"]["slicing"]["status"] == "failed"
    assert trace["stages"]["slicing"]["details"]["parts"]["base"]["status"] == "failed"
    assert trace["stages"]["slicing"]["details"]["parts"]["lid"]["status"] == "passed"
    assert trace["stages"]["slicing"]["issues"][0]["code"] == "SLICE_PROCESS_FAILED"


@pytest.mark.skipif(not available_engines(), reason="no geometry engine")
def test_trace_requested_slicing_unverified_is_not_passed(tmp_path, monkeypatch):
    import sparkplan.pipeline.pipeline as pipeline

    def fake_verify(base_stl, lid_stl, *, output_dir=None, enabled=True, **kwargs):
        return _slice_result(Path(base_stl), Path(lid_stl), output_dir, status="unverified")

    monkeypatch.setattr(pipeline, "verify_slice", fake_verify)
    result = run_pipeline(
        "desk weather station",
        FakeLLM(script=[VALID_SPEC]),
        out_dir=tmp_path,
        do_slice=True,
    )

    assert result.status == "ok"
    trace = json.loads((tmp_path / "trace.json").read_text())
    assert trace["status"] == "failed"
    assert trace["stages"]["slicing"]["status"] == "unverified"


def test_requested_slicing_without_out_dir_fails():
    result = run_pipeline(
        "desk weather station",
        FakeLLM(script=[VALID_SPEC]),
        do_slice=True,
    )

    assert result.status == "ok"
    assert result.slice_result is not None
    assert result.slice_result.status == "failed"
    assert result.slice_result.issues[0].code == "SLICE_OUTPUT_DIR_REQUIRED"


def test_trace_records_refused_plan(tmp_path):
    result = run_pipeline(
        "build me a GPS drone with a camera", FakeLLM(script=[]), out_dir=tmp_path
    )
    assert result.status == "refused"
    trace = json.loads((tmp_path / "trace.json").read_text())
    assert trace["status"] == "failed"
    assert set(trace["stages"]) == EXPECTED_STAGES
    assert trace["stages"]["plan"]["status"] == "failed"
    assert trace["stages"]["validation"]["status"] == "failed"
    for stage in (
        "geometry_layout",
        "geometry_meshes",
        "geometry_stl_export",
        "firmware_generation",
        "firmware_compile",
        "guide",
        "slicing",
    ):
        assert trace["stages"][stage]["status"] == "skipped"
        assert trace["stages"][stage]["reason"] == "not reached because planning was refused"
    assert trace["stages"]["simulation"]["status"] == "unverified"
    assert (
        trace["stages"]["simulation"]["reason"] == "Wokwi or hardware simulation was not performed"
    )


def test_trace_records_exhausted_plan(tmp_path):
    bad = dict(VALID_SPEC)
    bad["parts"] = [{"instance_id": "env", "part_id": "not_in_catalog"}]
    bad["connections"] = []
    bad["actions"] = []
    result = run_pipeline("weather station", FakeLLM(script=[bad] * 10), out_dir=tmp_path)
    assert result.status == "failed"
    trace = json.loads((tmp_path / "trace.json").read_text())
    assert trace["status"] == "failed"
    assert set(trace["stages"]) == EXPECTED_STAGES
    assert trace["stages"]["plan"]["status"] == "failed"
    assert trace["stages"]["validation"]["status"] == "failed"
    assert trace["stages"]["geometry_layout"]["status"] == "skipped"
    assert trace["stages"]["geometry_layout"]["reason"] == "not reached because planning failed"
    assert any(issue["code"] == "UNKNOWN_PART" for issue in trace["stages"]["validation"]["issues"])


def test_trace_serializes_issue_instances(tmp_path):
    bad = dict(VALID_SPEC)
    bad["parts"] = [{"instance_id": "env", "part_id": "not_in_catalog"}]
    bad["connections"] = []
    bad["actions"] = []
    run_pipeline("weather station", FakeLLM(script=[bad] * 10), out_dir=tmp_path)
    trace = json.loads((tmp_path / "trace.json").read_text())
    issue = trace["stages"]["validation"]["issues"][0]
    assert issue["code"] == "UNKNOWN_PART"
    assert issue["severity"] == "error"


def test_trace_no_file_without_output_or_path():
    result = run_pipeline("build me a GPS drone with a camera", FakeLLM(script=[]))
    assert result.trace_path is None
    assert not Path("trace.json").exists()


def test_trace_explicit_path(tmp_path):
    trace_path = tmp_path / "custom-trace.json"
    result = run_pipeline(
        "build me a GPS drone with a camera", FakeLLM(script=[]), trace_path=trace_path
    )
    assert result.trace_path == trace_path
    assert trace_path.is_file()


def test_trace_explicit_path_collision(tmp_path):
    trace_path = tmp_path / "trace.json"
    trace_path.write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError):
        run_pipeline(
            "build me a GPS drone with a camera", FakeLLM(script=[]), trace_path=trace_path
        )
    assert trace_path.read_text() == "existing"


def test_trace_multiple_runs_same_directory(tmp_path):
    first = run_pipeline("build me a GPS drone with a camera", FakeLLM(script=[]), out_dir=tmp_path)
    second = run_pipeline(
        "build me a GPS drone with a camera", FakeLLM(script=[]), out_dir=tmp_path
    )
    assert first.trace_path == tmp_path / "trace.json"
    assert second.trace_path != first.trace_path
    assert second.trace_path.is_file()
    trace_files = sorted(tmp_path.glob("trace*.json"))
    assert len(trace_files) == 2


@pytest.mark.skipif(not available_engines(), reason="no geometry engine")
def test_trace_geometry_layout_failure_completes_all_stages(tmp_path, monkeypatch):
    import sparkplan.pipeline.pipeline as pipeline
    from sparkplan.geometry.engine import GeometryEngineError

    def fail_layout(spec, catalog):
        raise GeometryEngineError("layout boom")

    monkeypatch.setattr(pipeline, "compute_enclosure_plan", fail_layout)
    result = run_pipeline("desk weather station", FakeLLM(script=[VALID_SPEC]), out_dir=tmp_path)
    assert result.status == "failed"
    assert any(issue.code == "GEOMETRY_FAILED" for issue in result.issues)
    trace = json.loads((tmp_path / "trace.json").read_text())
    assert trace["status"] == "failed"
    assert set(trace["stages"]) == EXPECTED_STAGES
    assert trace["stages"]["plan"]["status"] == "passed"
    assert trace["stages"]["validation"]["status"] == "passed"
    assert trace["stages"]["geometry_layout"]["status"] == "failed"
    for stage in (
        "geometry_meshes",
        "geometry_stl_export",
        "firmware_generation",
        "firmware_compile",
        "guide",
        "slicing",
    ):
        assert trace["stages"][stage]["status"] == "skipped"
        assert trace["stages"][stage]["reason"] == "not reached because geometry layout failed"
    assert trace["stages"]["simulation"]["status"] == "unverified"


@pytest.mark.skipif(not available_engines(), reason="no geometry engine")
def test_trace_geometry_failure_preserves_result(tmp_path, monkeypatch):
    import sparkplan.pipeline.pipeline as pipeline
    from sparkplan.geometry.engine import GeometryEngineError

    def fail_build(plan):
        raise GeometryEngineError("boom")

    monkeypatch.setattr(pipeline, "build_enclosure", fail_build)
    result = run_pipeline("desk weather station", FakeLLM(script=[VALID_SPEC]), out_dir=tmp_path)
    assert result.status == "failed"
    assert any(issue.code == "GEOMETRY_FAILED" for issue in result.issues)
    trace = json.loads((tmp_path / "trace.json").read_text())
    assert trace["status"] == "failed"
    assert set(trace["stages"]) == EXPECTED_STAGES
    assert trace["stages"]["plan"]["status"] == "passed"
    assert trace["stages"]["validation"]["status"] == "passed"
    assert trace["stages"]["geometry_layout"]["status"] == "passed"
    assert trace["stages"]["geometry_meshes"]["status"] == "failed"
    for stage in (
        "geometry_stl_export",
        "firmware_generation",
        "firmware_compile",
        "guide",
        "slicing",
    ):
        assert trace["stages"][stage]["status"] == "skipped"
        assert trace["stages"][stage]["reason"] == "not reached because mesh generation failed"
    assert trace["stages"]["simulation"]["status"] == "unverified"


@pytest.mark.skipif(not available_engines(), reason="no geometry engine")
def test_trace_firmware_generation_failure_completes_all_stages(tmp_path, monkeypatch):
    import sparkplan.pipeline.pipeline as pipeline
    from sparkplan.firmware import FirmwareError

    def fail_generate(spec, catalog):
        raise FirmwareError("firmware boom")

    monkeypatch.setattr(pipeline, "generate_firmware", fail_generate)
    result = run_pipeline("desk weather station", FakeLLM(script=[VALID_SPEC]), out_dir=tmp_path)
    assert result.status == "failed"
    assert any(issue.code == "FIRMWARE_FAILED" for issue in result.issues)
    trace = json.loads((tmp_path / "trace.json").read_text())
    assert trace["status"] == "failed"
    assert set(trace["stages"]) == EXPECTED_STAGES
    assert trace["stages"]["plan"]["status"] == "passed"
    assert trace["stages"]["validation"]["status"] == "passed"
    assert trace["stages"]["geometry_layout"]["status"] == "passed"
    assert trace["stages"]["geometry_meshes"]["status"] == "passed"
    assert trace["stages"]["geometry_stl_export"]["status"] == "passed"
    assert trace["stages"]["firmware_generation"]["status"] == "failed"
    for stage in ("firmware_compile", "guide", "slicing"):
        assert trace["stages"][stage]["status"] == "skipped"
        assert trace["stages"][stage]["reason"] == "not reached because firmware generation failed"
    assert trace["stages"]["simulation"]["status"] == "unverified"


@pytest.mark.skipif(not available_engines(), reason="no geometry engine")
def test_trace_unexpected_exception_propagates(tmp_path, monkeypatch):
    import sparkplan.pipeline.pipeline as pipeline

    def fail_plan(spec, catalog):
        raise ValueError("unexpected")

    monkeypatch.setattr(pipeline, "compute_enclosure_plan", fail_plan)
    with pytest.raises(ValueError, match="unexpected"):
        run_pipeline("desk weather station", FakeLLM(script=[VALID_SPEC]), out_dir=tmp_path)
    assert (tmp_path / "trace.json").is_file()
    trace = json.loads((tmp_path / "trace.json").read_text())
    assert trace["status"] == "failed"
    assert set(trace["stages"]) == EXPECTED_STAGES
    assert trace["stages"]["plan"]["status"] == "passed"
    assert trace["stages"]["validation"]["status"] == "passed"
    assert trace["stages"]["geometry_layout"]["status"] == "failed"
    assert (
        trace["stages"]["geometry_layout"]["reason"]
        == "unexpected ValueError while running this stage"
    )
    for stage in (
        "geometry_meshes",
        "geometry_stl_export",
        "firmware_generation",
        "firmware_compile",
        "guide",
        "slicing",
    ):
        assert trace["stages"][stage]["status"] == "skipped"
        assert (
            trace["stages"][stage]["reason"]
            == "not reached because an earlier stage raised an unexpected exception"
        )
    assert trace["stages"]["simulation"]["status"] == "unverified"


@pytest.mark.skipif(not available_engines(), reason="no geometry engine")
def test_trace_write_failure_does_not_mask_original(tmp_path, monkeypatch):
    import sparkplan.pipeline.pipeline as pipeline

    def fail_plan(spec, catalog):
        raise ValueError("original")

    def fail_write(trace, path):
        raise RuntimeError("trace write failed")

    monkeypatch.setattr(pipeline, "compute_enclosure_plan", fail_plan)
    monkeypatch.setattr(pipeline, "write_trace", fail_write)
    with pytest.raises(ValueError, match="original"):
        run_pipeline("desk weather station", FakeLLM(script=[VALID_SPEC]), out_dir=tmp_path)


def test_trace_firmware_compile_skipped(tmp_path):
    run_pipeline("build me a GPS drone with a camera", FakeLLM(script=[]), out_dir=tmp_path)
    trace = json.loads((tmp_path / "trace.json").read_text())
    assert trace["stages"]["firmware_compile"]["status"] == "skipped"


@pytest.mark.skipif(not available_engines(), reason="no geometry engine")
def test_trace_compile_failure_forces_failed_status(tmp_path, monkeypatch):
    import stat
    import textwrap

    from sparkplan.pipeline import run_pipeline

    cli = tmp_path / "arduino-cli"
    cli.write_text(textwrap.dedent("""\
            #!/bin/sh
            echo "sketch.ino:1: error: boom" >&2
            exit 1
            """))
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("SPARKPLAN_ARDUINO_CLI", str(cli))

    result = run_pipeline(
        "desk weather station",
        FakeLLM(script=[VALID_SPEC]),
        out_dir=tmp_path,
        do_compile=True,
    )
    assert result.status == "ok"
    trace = json.loads((tmp_path / "trace.json").read_text())
    assert trace["status"] == "failed"
    assert set(trace["stages"]) == EXPECTED_STAGES
    assert trace["stages"]["plan"]["status"] == "passed"
    assert trace["stages"]["validation"]["status"] == "passed"
    assert trace["stages"]["geometry_layout"]["status"] == "passed"
    assert trace["stages"]["geometry_meshes"]["status"] == "passed"
    assert trace["stages"]["geometry_stl_export"]["status"] == "passed"
    assert trace["stages"]["firmware_generation"]["status"] == "passed"
    assert trace["stages"]["firmware_compile"]["status"] == "failed"
    assert trace["stages"]["guide"]["status"] == "passed"
    assert trace["stages"]["slicing"]["status"] == "skipped"
    assert trace["stages"]["simulation"]["status"] == "unverified"


@pytest.mark.skipif(not available_engines(), reason="no geometry engine")
def test_trace_successful_compile_with_all_outputs_is_passed(tmp_path, monkeypatch):
    import stat
    import textwrap

    cli = tmp_path / "arduino-cli"
    cli.write_text(textwrap.dedent("""\
            #!/bin/sh
            echo "Sketch uses 123 bytes (1%) of program storage space."
            exit 0
            """))
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("SPARKPLAN_ARDUINO_CLI", str(cli))

    result = run_pipeline(
        "desk weather station",
        FakeLLM(script=[VALID_SPEC]),
        out_dir=tmp_path,
        do_compile=True,
    )
    assert result.status == "ok"
    trace = json.loads((tmp_path / "trace.json").read_text())
    assert trace["status"] == "passed"
    assert trace["stages"]["firmware_compile"]["status"] == "passed"


@pytest.mark.skipif(not available_engines(), reason="no geometry engine")
def test_trace_successful_compile_with_missing_output_is_not_passed(tmp_path, monkeypatch):
    import stat
    import textwrap

    cli = tmp_path / "arduino-cli"
    cli.write_text(textwrap.dedent("""\
            #!/bin/sh
            echo "Sketch uses 123 bytes (1%) of program storage space."
            rm "$6/sparkplan.ino"
            exit 0
            """))
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("SPARKPLAN_ARDUINO_CLI", str(cli))

    result = run_pipeline(
        "desk weather station",
        FakeLLM(script=[VALID_SPEC]),
        out_dir=tmp_path,
        do_compile=True,
    )
    assert result.status == "ok"
    trace = json.loads((tmp_path / "trace.json").read_text())
    assert trace["status"] == "failed"
    assert trace["stages"]["firmware_compile"]["status"] == "failed"
    assert trace["stages"]["firmware_compile"]["issues"][0]["code"] == "COMPILE_OUTPUT_MISSING"


def test_trace_explicit_path_preserved_byte_for_byte(tmp_path):
    trace_path = tmp_path / "trace.json"
    original = b"existing trace bytes"
    trace_path.write_bytes(original)
    with pytest.raises(FileExistsError):
        run_pipeline(
            "build me a GPS drone with a camera", FakeLLM(script=[]), trace_path=trace_path
        )
    assert trace_path.read_bytes() == original


def test_claim_trace_path_collision(tmp_path):
    from sparkplan.trace import claim_trace_path

    path = tmp_path / "trace.json"
    claimed = claim_trace_path(path)
    assert claimed == path
    assert path.exists()
    with pytest.raises(FileExistsError):
        claim_trace_path(path)


def test_claim_trace_path_race_is_deterministic(tmp_path, monkeypatch):
    import sparkplan.trace as trace_module

    path = tmp_path / "trace.json"
    real_open = trace_module.os.open
    calls = {"count": 0}

    def fake_open(*args, **kwargs):
        if args[1] & os.O_EXCL:
            calls["count"] += 1
            if calls["count"] == 1:
                return real_open(*args, **kwargs)
            raise FileExistsError
        return real_open(*args, **kwargs)

    monkeypatch.setattr(trace_module.os, "open", fake_open)
    claimed = trace_module.claim_trace_path(path)
    assert claimed == path
    with pytest.raises(FileExistsError):
        trace_module.claim_trace_path(path)


def test_default_trace_paths_do_not_overwrite_under_claim_collision(tmp_path, monkeypatch):
    import sparkplan.pipeline.pipeline as pipeline

    real_claim = pipeline.claim_trace_path
    calls = {"count": 0}

    def fake_claim(path):
        calls["count"] += 1
        if calls["count"] == 1:
            return real_claim(path)
        if Path(path).name == "trace.json":
            raise FileExistsError("simulated collision")
        return real_claim(path)

    monkeypatch.setattr(pipeline, "claim_trace_path", fake_claim)
    first = run_pipeline("build me a GPS drone with a camera", FakeLLM(script=[]), out_dir=tmp_path)
    second = run_pipeline(
        "build me a GPS drone with a camera", FakeLLM(script=[]), out_dir=tmp_path
    )
    assert first.trace_path == tmp_path / "trace.json"
    assert second.trace_path != first.trace_path
    assert second.trace_path.is_file()


def test_trace_finalization_preserves_original_exception(tmp_path, monkeypatch):
    import sparkplan.pipeline.pipeline as pipeline

    def fail_plan(spec, catalog):
        raise ValueError("original failure")

    def fail_write(trace, path):
        raise RuntimeError("trace write failed")

    monkeypatch.setattr(pipeline, "compute_enclosure_plan", fail_plan)
    monkeypatch.setattr(pipeline, "write_trace", fail_write)
    with pytest.raises(ValueError, match="original failure"):
        run_pipeline("desk weather station", FakeLLM(script=[VALID_SPEC]), out_dir=tmp_path)
