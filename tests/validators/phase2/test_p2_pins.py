from __future__ import annotations

import pytest
from sparkplan.models.buildspec import parse_build_spec
from sparkplan.models.catalog import get_catalog
from sparkplan.validators.phase2.pins_check import validate_pins
from tests.validators.phase2.conftest import error_codes, run


def test_unknown_pin_fails():
    assert "PIN_UNKNOWN" in error_codes(run("v2/unknown_pin.json"))


def test_analog_on_non_adc_pin_fails():
    assert "GPIO_NOT_ADC_HERE" in error_codes(run("v2/adc_missing.json"))


def test_flash_pin_fails():
    assert "GPIO_FLASH_RESERVED" in error_codes(run("v2/flash_pin.json"))


@pytest.mark.parametrize("uart0_pin", ["D1", "D3", "GPIO1", "GPIO3"])
def test_uart0_reserved_pin_fails(uart0_pin: str):
    cat = get_catalog()
    board = cat.board().board
    spec_data = {
        "status": "ok",
        "title": "uart0 test",
        "summary": "uart0 reserved pin check",
        "parts": [
            {"instance_id": "board", "part_id": "esp32_devkit_30pin"},
            {"instance_id": "env", "part_id": "dht22"},
        ],
        "connections": [
            {
                "from_instance": "env",
                "from_pin": "DATA",
                "to_instance": "board",
                "to_pin": uart0_pin,
                "signal_role": "signal",
            }
        ],
        "layout": [],
        "behaviors": [],
    }
    spec = parse_build_spec(spec_data)
    issues = validate_pins(spec, cat, board)
    codes = {i.code for i in issues}
    assert "GPIO_RESERVED" in codes


def test_valid_passes():
    report = run("v2/valid.json")
    assert report.passed, [i.code for i in report.errors]
