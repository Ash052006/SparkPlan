from __future__ import annotations

from tests.validators.phase2.conftest import error_codes, run


def test_missing_signal_pin_fails():
    assert "PIN_UNCONNECTED" in error_codes(run("v7/missing_signal.json"))


def test_missing_scl_pin_fails():
    assert "PIN_UNCONNECTED" in error_codes(run("v7/missing_scl.json"))


def test_unconnected_part_fails():
    assert "PIN_UNCONNECTED" in error_codes(run("v7/unconnected_button.json"))


def test_valid_passes():
    report = run("v7/valid.json")
    assert report.passed, [i.code for i in report.errors]
