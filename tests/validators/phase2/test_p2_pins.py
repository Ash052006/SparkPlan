from __future__ import annotations

from tests.validators.phase2.conftest import error_codes, run


def test_unknown_pin_fails():
    assert "PIN_UNKNOWN" in error_codes(run("v2/unknown_pin.json"))


def test_analog_on_non_adc_pin_fails():
    assert "GPIO_NOT_ADC_HERE" in error_codes(run("v2/adc_missing.json"))


def test_flash_pin_fails():
    assert "GPIO_FLASH_RESERVED" in error_codes(run("v2/flash_pin.json"))


def test_valid_passes():
    report = run("v2/valid.json")
    assert report.passed, [i.code for i in report.errors]
