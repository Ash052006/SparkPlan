from __future__ import annotations

from tests.validators.phase2.conftest import error_codes, run


def test_two_oled_same_address_fails():
    assert "I2C_ADDRESS_CONFLICT" in error_codes(run("v6/address_conflict.json"))


def test_two_bme280_same_address_fails():
    assert "I2C_ADDRESS_CONFLICT" in error_codes(run("v6/address_conflict_0x76.json"))


def test_split_bus_fails():
    assert "I2C_BUS_SPLIT" in error_codes(run("v6/bus_split.json"))


def test_valid_passes():
    report = run("v6/valid.json")
    assert report.passed, [i.code for i in report.errors]
