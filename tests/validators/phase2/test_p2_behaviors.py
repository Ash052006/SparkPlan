from __future__ import annotations

from tests.validators.phase2.conftest import error_codes, run


def test_unknown_reading_fails():
    assert "BEHAVIOR_UNKNOWN_READING" in error_codes(run("v8/unknown_reading.json"))


def test_unit_mismatch_fails():
    assert "BEHAVIOR_UNIT_MISMATCH" in error_codes(run("v8/unit_mismatch.json"))


def test_unknown_actuator_fails():
    assert "BEHAVIOR_UNKNOWN_ACTUATOR" in error_codes(run("v8/unknown_actuator.json"))


def test_valid_passes():
    report = run("v8/valid.json")
    assert report.passed, [i.code for i in report.errors]
