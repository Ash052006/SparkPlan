from __future__ import annotations

from tests.validators.phase2.conftest import error_codes, run


def test_unknown_part_fails():
    assert "UNKNOWN_PART" in error_codes(run("v1/unknown_part.json"))


def test_duplicate_instance_fails():
    assert "DUPLICATE_INSTANCE" in error_codes(run("v1/duplicate_instance.json"))


def test_board_missing_fails():
    assert "BOARD_MISSING" in error_codes(run("v1/board_missing.json"))


def test_valid_passes():
    report = run("v1/valid.json")
    assert report.passed, [i.code for i in report.errors]
