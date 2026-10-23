"""Slicer verification tests; subprocesses are mocked here."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import sparkplan.geometry.slicing as slicing

STL = b"solid sparkplan\nendsolid sparkplan\n"


def _stls(tmp_path: Path) -> tuple[Path, Path]:
    base = tmp_path / "enclosure_base.stl"
    lid = tmp_path / "enclosure_lid.stl"
    base.write_bytes(STL)
    lid.write_bytes(STL)
    return base, lid


def _mock_slicer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slicing, "resolve_slicer", lambda explicit=None: "/usr/bin/prusa-slicer")
    monkeypatch.setattr(slicing, "slicer_version", lambda slicer, timeout_s=10.0: "2.7.2")


def _run_fake(
    command: list[str],
    *,
    fail: set[str] | None = None,
    missing: set[str] | None = None,
    empty: set[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    input_path = Path(command[-1])
    output_path = Path(command[command.index("--output") + 1])
    fail = fail or set()
    missing = missing or set()
    empty = empty or set()
    if input_path.stem in fail:
        return subprocess.CompletedProcess(command, 2, stdout="", stderr="slicer failed")
    if input_path.stem not in missing:
        output_path.write_text("; gcode\n" if input_path.stem not in empty else "")
    return subprocess.CompletedProcess(command, 0, stdout="exported", stderr="")


def test_successful_slicing_requires_both_outputs(tmp_path, monkeypatch):
    _mock_slicer(monkeypatch)
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return _run_fake(command)

    monkeypatch.setattr(slicing.subprocess, "run", fake_run)
    base, lid = _stls(tmp_path)
    output = tmp_path / "gcode"
    result = slicing.verify_slice(base, lid, output_dir=output)

    assert result.status == "passed"
    assert result.slicer == "/usr/bin/prusa-slicer"
    assert result.version == "2.7.2"
    assert {name: part.status for name, part in result.parts.items()} == {
        "base": "passed",
        "lid": "passed",
    }
    assert (output / "enclosure_base.gcode").stat().st_size > 0
    assert (output / "enclosure_lid.gcode").stat().st_size > 0
    assert len(calls) == 2
    assert calls[0][calls[0].index("--load") + 1] == str(slicing.DEFAULT_PROFILE)
    assert "--datadir" in calls[0]


def test_missing_and_empty_inputs_fail_before_slicer_launch(tmp_path, monkeypatch):
    lid = tmp_path / "enclosure_lid.stl"
    lid.write_bytes(b"")

    def boom(*args, **kwargs):
        raise AssertionError("subprocess must not run for invalid inputs")

    monkeypatch.setattr(slicing.subprocess, "run", boom)
    result = slicing.verify_slice(
        tmp_path / "enclosure_base.stl",
        lid,
        slicer=tmp_path / "missing-prusa-slicer",
    )

    assert result.status == "failed"
    assert result.parts["base"].status == "failed"
    assert result.parts["lid"].status == "failed"
    assert result.parts["base"].issues[0].code == "SLICE_INPUT_MISSING"
    assert result.parts["lid"].issues[0].code == "SLICE_INPUT_EMPTY"


def test_missing_slicer_is_unverified(tmp_path, monkeypatch):
    base, lid = _stls(tmp_path)

    def boom(*args, **kwargs):
        raise AssertionError("subprocess must not run without a slicer")

    monkeypatch.setattr(slicing.subprocess, "run", boom)
    result = slicing.verify_slice(
        base,
        lid,
        slicer=tmp_path / "missing-prusa-slicer",
    )

    assert result.status == "unverified"
    assert result.parts["base"].status == "unverified"
    assert result.parts["lid"].status == "unverified"
    assert result.issues[0].code == "SLICER_UNAVAILABLE"


def test_missing_profile_fails_before_slicer_launch(tmp_path, monkeypatch):
    base, lid = _stls(tmp_path)

    def boom(*args, **kwargs):
        raise AssertionError("subprocess must not run without a profile")

    monkeypatch.setattr(slicing.subprocess, "run", boom)
    result = slicing.verify_slice(
        base,
        lid,
        profile=tmp_path / "missing.ini",
    )

    assert result.status == "failed"
    assert result.issues[0].code == "SLICE_PROFILE_MISSING"


def test_timeout_fails_both_parts(tmp_path, monkeypatch):
    _mock_slicer(monkeypatch)
    base, lid = _stls(tmp_path)

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], output="partial", stderr="slow")

    monkeypatch.setattr(slicing.subprocess, "run", fake_run)
    result = slicing.verify_slice(base, lid)

    assert result.status == "failed"
    assert result.parts["base"].status == "failed"
    assert result.parts["lid"].status == "failed"
    assert {issue.code for issue in result.issues} == {"SLICE_TIMEOUT"}


def test_nonzero_exit_fails(tmp_path, monkeypatch):
    _mock_slicer(monkeypatch)
    base, lid = _stls(tmp_path)

    def fake_run(command, **kwargs):
        return _run_fake(command, fail={"enclosure_base", "enclosure_lid"})

    monkeypatch.setattr(slicing.subprocess, "run", fake_run)
    result = slicing.verify_slice(base, lid)

    assert result.status == "failed"
    assert result.parts["base"].exit_code == 2
    assert {issue.code for issue in result.issues} == {"SLICE_PROCESS_FAILED"}


def test_zero_exit_missing_output_fails(tmp_path, monkeypatch):
    _mock_slicer(monkeypatch)
    base, lid = _stls(tmp_path)

    def fake_run(command, **kwargs):
        return _run_fake(command, missing={"enclosure_base", "enclosure_lid"})

    monkeypatch.setattr(slicing.subprocess, "run", fake_run)
    result = slicing.verify_slice(base, lid)

    assert result.status == "failed"
    assert {issue.code for issue in result.issues} == {"SLICE_OUTPUT_MISSING"}


def test_zero_exit_empty_output_fails(tmp_path, monkeypatch):
    _mock_slicer(monkeypatch)
    base, lid = _stls(tmp_path)

    def fake_run(command, **kwargs):
        return _run_fake(command, empty={"enclosure_base", "enclosure_lid"})

    monkeypatch.setattr(slicing.subprocess, "run", fake_run)
    result = slicing.verify_slice(base, lid)

    assert result.status == "failed"
    assert {issue.code for issue in result.issues} == {"SLICE_OUTPUT_EMPTY"}


def test_base_and_lid_failures_are_separate(tmp_path, monkeypatch):
    _mock_slicer(monkeypatch)
    base, lid = _stls(tmp_path)

    def fake_run(command, **kwargs):
        return _run_fake(command, fail={"enclosure_base"})

    monkeypatch.setattr(slicing.subprocess, "run", fake_run)
    result = slicing.verify_slice(base, lid)

    assert result.status == "failed"
    assert result.parts["base"].status == "failed"
    assert result.parts["lid"].status == "passed"
    assert result.parts["lid"].output_path is not None
    assert Path(result.parts["lid"].output_path).stat().st_size > 0


def test_paths_with_spaces_are_passed_as_single_arguments(tmp_path, monkeypatch):
    _mock_slicer(monkeypatch)
    work = tmp_path / "dir with spaces"
    work.mkdir()
    base = work / "base part.stl"
    lid = work / "lid part.stl"
    base.write_bytes(STL)
    lid.write_bytes(STL)
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return _run_fake(command)

    monkeypatch.setattr(slicing.subprocess, "run", fake_run)
    result = slicing.verify_slice(base, lid, output_dir=work / "gcode output")

    assert result.status == "passed"
    assert calls[0][-1] == str(base)
    assert calls[1][-1] == str(lid)
    assert (work / "gcode output" / "base part.gcode").stat().st_size > 0
    assert (work / "gcode output" / "lid part.gcode").stat().st_size > 0


def test_disabled_is_skipped_without_input_checks(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("disabled slicing must not inspect inputs")

    monkeypatch.setattr(slicing.subprocess, "run", boom)
    result = slicing.verify_slice(
        tmp_path / "missing-base.stl",
        tmp_path / "missing-lid.stl",
        enabled=False,
    )

    assert result.status == "skipped"
    assert result.reason == "slicing was not requested"
    assert result.parts == {}


def test_slicer_version_is_read_from_help(tmp_path, monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="PrusaSlicer-2.7.2+UNKNOWN based on Slic3r (with GUI support)\n",
            stderr="",
        )

    monkeypatch.setattr(slicing.subprocess, "run", fake_run)
    assert slicing.slicer_version("/usr/bin/prusa-slicer") == "2.7.2+UNKNOWN"


def test_diagnostics_are_sanitized_and_truncated(tmp_path, monkeypatch):
    _mock_slicer(monkeypatch)
    base, lid = _stls(tmp_path)

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"home={Path.home()} token=supersecret\n" + ("x" * 5000),
            stderr="",
        )

    monkeypatch.setattr(slicing.subprocess, "run", fake_run)
    result = slicing.verify_slice(base, lid)

    diagnostics = result.parts["base"].diagnostics
    assert "supersecret" not in diagnostics
    assert "[redacted]" in diagnostics
    assert str(Path.home()) not in diagnostics
    assert "~" in diagnostics
    assert "truncated" in diagnostics
