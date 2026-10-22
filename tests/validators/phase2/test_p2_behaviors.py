from __future__ import annotations

from sparkplan.validators.phase2 import run_all
from tests.validators.phase2.conftest import error_codes, load, run


def test_unknown_reading_fails():
    assert "BEHAVIOR_UNKNOWN_READING" in error_codes(run("v8/unknown_reading.json"))


def test_behavior_reading_must_be_provided_by_the_bound_part():
    spec = load("v8/valid.json")
    spec.behaviors[0].readings[0].name = "moisture"
    report = run_all(spec)
    assert "BEHAVIOR_UNKNOWN_READING" in {i.code for i in report.errors}
    unknown = [i for i in report.errors if i.code == "BEHAVIOR_UNKNOWN_READING"]
    assert "moisture" in unknown[0].message
    assert "soil_raw" in unknown[0].fix_hint


def test_behavior_reading_unit_must_match_catalog():
    spec = load("v8/valid.json")
    spec.behaviors[0].readings[0].unit = "percent"
    report = run_all(spec)
    assert "BEHAVIOR_UNIT_MISMATCH" in {i.code for i in report.errors}


def test_behavior_reading_must_bind_to_an_instance():
    spec = load("v8/valid.json")
    spec.behaviors[0].readings[0].instance_id = ""
    report = run_all(spec)
    assert "BEHAVIOR_UNBOUND_READING" in {i.code for i in report.errors}


def test_unit_mismatch_fails():
    assert "BEHAVIOR_UNIT_MISMATCH" in error_codes(run("v8/unit_mismatch.json"))


def test_unknown_actuator_fails():
    assert "BEHAVIOR_UNKNOWN_ACTUATOR" in error_codes(run("v8/unknown_actuator.json"))


def test_valid_passes():
    report = run("v8/valid.json")
    assert report.passed, [i.code for i in report.errors]
