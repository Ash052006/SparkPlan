from __future__ import annotations

from tests.validators.phase2.conftest import error_codes, run


def test_no_power_fails():
    assert "NO_POWER" in error_codes(run("v4/no_power.json"))


def test_no_ground_fails():
    assert "NO_GROUND" in error_codes(run("v4/no_ground.json"))


def test_over_budget_fails():
    assert "RAIL_OVER_BUDGET" in error_codes(run("v4/over_budget.json"))


def test_over_budget_message_names_peak_current():
    report = run("v4/over_budget.json")
    over = [i for i in report.errors if i.code == "RAIL_OVER_BUDGET"]
    assert over, "expected a RAIL_OVER_BUDGET error"
    assert any("peak current" in i.message for i in over)


def test_valid_passes():
    report = run("v4/valid.json")
    assert report.passed, [i.code for i in report.errors]
