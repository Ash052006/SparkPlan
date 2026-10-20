from __future__ import annotations

from tests.validators.phase2.conftest import error_codes, run


def test_soil_monitor_is_fully_valid():
    report = run("required/soil_monitor_valid.json")
    assert report.passed, [i.code for i in report.errors]
    assert report.issues == []


def test_hc_sr04_echo_direct_fails_v5():
    assert "LOGIC_LEVEL_HIGH" in error_codes(run("required/hc_sr04_echo_direct.json"))


def test_two_oled_0x3c_fails_v6():
    assert "I2C_ADDRESS_CONFLICT" in error_codes(run("required/two_oled_0x3c.json"))


def test_over_budget_fails_v4():
    assert "RAIL_OVER_BUDGET" in error_codes(run("required/over_budget.json"))


def test_gpio6_fails_v2():
    assert "GPIO_FLASH_RESERVED" in error_codes(run("required/gpio6.json"))
