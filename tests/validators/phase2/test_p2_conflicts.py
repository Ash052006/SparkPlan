from __future__ import annotations

from tests.validators.phase2.conftest import error_codes, run


def test_gpio_conflict_fails():
    assert "GPIO_CONFLICT" in error_codes(run("v3/gpio_conflict.json"))


def test_gpio_conflict_trig_fails():
    assert "GPIO_CONFLICT" in error_codes(run("v3/gpio_conflict_trig.json"))


def test_gpio_conflict_echo_fails():
    assert "GPIO_CONFLICT" in error_codes(run("v3/gpio_conflict_echo.json"))


def test_valid_passes():
    report = run("v3/valid.json")
    assert report.passed, [i.code for i in report.errors]
