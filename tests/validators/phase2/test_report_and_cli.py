from __future__ import annotations

import subprocess
import sys

from sparkplan.validators.phase2 import run_all
from tests.validators.phase2.conftest import FIXTURES, codes, load, run

EXPECTED_VALIDATORS = {
    "V1_membership",
    "V2_pins",
    "V3_conflicts",
    "V4_power",
    "V5_logic_levels",
    "V6_i2c",
    "V7_completeness",
    "V8_behaviors",
    "V9_layout",
}


def test_report_has_all_nine_validators():
    report = run_all(load("v1/valid.json"))
    assert set(report.timings_ms) == EXPECTED_VALIDATORS


def test_report_passed_and_timings_are_non_negative():
    report = run_all(load("v1/valid.json"))
    assert report.passed is True
    assert all(ms >= 0 for ms in report.timings_ms.values())


def test_report_by_code():
    report = run("v1/unknown_part.json")
    assert report.by_code().get("UNKNOWN_PART") == 1
    assert report.passed is False


def test_validator_crash_is_reported_not_raised():
    report = run("v1/valid.json")
    assert "VALIDATOR_CRASH" not in codes(report)


def test_cli_prints_report_for_valid_spec():
    spec = FIXTURES / "v1" / "valid.json"
    proc = subprocess.run(
        [sys.executable, "-m", "sparkplan.validators.phase2", str(spec)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "Validation: PASS" in proc.stdout


def test_cli_exit_code_one_for_failing_spec():
    spec = FIXTURES / "v1" / "unknown_part.json"
    proc = subprocess.run(
        [sys.executable, "-m", "sparkplan.validators.phase2", str(spec)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1
    assert "UNKNOWN_PART" in proc.stdout


def test_cli_usage_error():
    proc = subprocess.run(
        [sys.executable, "-m", "sparkplan.validators.phase2"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2


def test_cli_missing_file():
    proc = subprocess.run(
        [sys.executable, "-m", "sparkplan.validators.phase2", "/no/such/spec.json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "could not read" in proc.stderr
