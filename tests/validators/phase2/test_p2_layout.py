from __future__ import annotations

from tests.validators.phase2.conftest import error_codes, run


def test_missing_hint_fails():
    assert "NO_MOUNT_ZONE" in error_codes(run("v9/missing_hint.json"))


def test_extra_part_without_hint_fails():
    assert "NO_MOUNT_ZONE" in error_codes(run("v9/no_hint_button_wallpart.json"))


def test_wall_part_without_exposure_fails():
    assert "NO_EXPOSURE" in error_codes(run("v9/wall_part_no_exposure.json"))


def test_valid_passes():
    report = run("v9/valid.json")
    assert report.passed, [i.code for i in report.errors]
