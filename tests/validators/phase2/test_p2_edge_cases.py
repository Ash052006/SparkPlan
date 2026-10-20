"""Edge-case unit tests that exercise branches not covered by JSON fixtures."""

from __future__ import annotations

from sparkplan.models.buildspec import parse_build_spec
from sparkplan.models.catalog import get_catalog
from sparkplan.validators.phase2 import run_all
from sparkplan.validators.phase2.i2c import validate_i2c
from sparkplan.validators.phase2.logic_levels import validate_logic_levels
from sparkplan.validators.phase2.membership import validate_membership
from sparkplan.validators.phase2.pins_check import validate_pins
from sparkplan.validators.phase2.power_check import validate_power


def _spec(**kw):
    base = {
        "status": "ok",
        "title": "t",
        "summary": "s",
        "parts": [{"instance_id": "board", "part_id": "esp32_devkit_30pin"}],
        "connections": [],
        "layout": [],
        "behaviors": [],
    }
    base.update(kw)
    from sparkplan.models.buildspec import SupportedBuild

    spec = parse_build_spec(base)
    assert isinstance(spec, SupportedBuild)
    return spec


def _board():
    cat = get_catalog()
    return cat, cat.board().board


def test_controller_pin_not_numbered():
    cat, board = _board()
    spec = _spec(
        parts=[
            {"instance_id": "board", "part_id": "esp32_devkit_30pin"},
            {"instance_id": "env", "part_id": "dht22"},
        ],
        connections=[
            {
                "from_instance": "env",
                "from_pin": "DATA",
                "to_instance": "board",
                "to_pin": "3V3",
                "signal_role": "signal",
            }
        ],
    )
    codes = {i.code for i in validate_pins(spec, cat, board)}
    assert "CONTROLLER_PIN_MISSING" in codes


def test_controller_pin_does_not_exist():
    cat, board = _board()
    spec = _spec(
        parts=[
            {"instance_id": "board", "part_id": "esp32_devkit_30pin"},
            {"instance_id": "env", "part_id": "dht22"},
        ],
        connections=[
            {
                "from_instance": "env",
                "from_pin": "DATA",
                "to_instance": "board",
                "to_pin": "GPIO99",
                "signal_role": "signal",
            }
        ],
    )
    codes = {i.code for i in validate_pins(spec, cat, board)}
    assert "CONTROLLER_PIN_MISSING" in codes


def test_board_duplicated():
    cat, board = _board()
    spec = _spec(
        parts=[
            {"instance_id": "board", "part_id": "esp32_devkit_30pin"},
            {"instance_id": "board2", "part_id": "esp32_devkit_30pin"},
        ]
    )
    codes = {i.code for i in validate_membership(spec, cat, board)}
    assert "BOARD_DUPLICATED" in codes


def test_power_unknown_rail_and_out_of_range():
    cat, board = _board()
    spec = _spec(
        parts=[
            {"instance_id": "board", "part_id": "esp32_devkit_30pin"},
            {"instance_id": "bme", "part_id": "bme280"},
        ],
        connections=[
            {
                "from_instance": "bme",
                "from_pin": "VCC",
                "to_instance": "board",
                "to_pin": "5V",
                "signal_role": "rail",
                "rail_name": "5V",
            },
            {
                "from_instance": "bme",
                "from_pin": "GND",
                "to_instance": "board",
                "to_pin": "GND",
                "signal_role": "gnd",
            },
        ],
    )
    codes = {i.code for i in validate_power(spec, cat, board)}
    # BME280 max is 3.6 V; 5 V rail is out of range
    assert "RAIL_OUT_OF_RANGE" in codes


def test_power_unknown_rail_name():
    cat, board = _board()
    spec = _spec(
        parts=[
            {"instance_id": "board", "part_id": "esp32_devkit_30pin"},
            {"instance_id": "env", "part_id": "dht22"},
        ],
        connections=[
            {
                "from_instance": "env",
                "from_pin": "VCC",
                "to_instance": "board",
                "to_pin": "VBAT",
                "signal_role": "rail",
                "rail_name": "VBAT",
            },
            {
                "from_instance": "env",
                "from_pin": "GND",
                "to_instance": "board",
                "to_pin": "GND",
                "signal_role": "gnd",
            },
        ],
    )
    codes = {i.code for i in validate_power(spec, cat, board)}
    assert "UNKNOWN_RAIL" in codes


def test_power_near_budget_warns():
    cat, board = _board()
    # 9 PIRs at 50 mA typical = 450 mA: above 80% of the 500 mA 5V budget but
    # still within it, so this must warn rather than error.
    pirs = [{"instance_id": "board", "part_id": "esp32_devkit_30pin"}]
    for i in range(1, 10):
        inst = "pir" if i == 1 else f"pir{i}"
        pirs.append({"instance_id": inst, "part_id": "pir_hc_sr501"})
    spec = _spec(parts=pirs, connections=_pirs_on_5v(9))
    issues = validate_power(spec, cat, board)
    assert any(i.code == "RAIL_NEAR_BUDGET" for i in issues)


def _pirs_on_5v(count: int):
    conns = []
    for i in range(1, count + 1):
        inst = "pir" if i == 1 else f"pir{i}"
        conns += [
            {
                "from_instance": inst,
                "from_pin": "VCC",
                "to_instance": "board",
                "to_pin": "5V",
                "signal_role": "rail",
                "rail_name": "5V",
            },
            {
                "from_instance": inst,
                "from_pin": "GND",
                "to_instance": "board",
                "to_pin": "GND",
                "signal_role": "gnd",
            },
        ]
    return conns


def test_i2c_scl_split():
    cat, board = _board()
    spec = _spec(
        parts=[
            {"instance_id": "board", "part_id": "esp32_devkit_30pin"},
            {"instance_id": "screen", "part_id": "ssd1306_oled_096"},
            {"instance_id": "baro", "part_id": "bme280"},
        ],
        connections=[
            {
                "from_instance": "screen",
                "from_pin": "SDA",
                "to_instance": "board",
                "to_pin": "D21",
                "signal_role": "sda",
            },
            {
                "from_instance": "screen",
                "from_pin": "SCL",
                "to_instance": "board",
                "to_pin": "D22",
                "signal_role": "scl",
            },
            {
                "from_instance": "baro",
                "from_pin": "SDA",
                "to_instance": "board",
                "to_pin": "D21",
                "signal_role": "sda",
            },
            {
                "from_instance": "baro",
                "from_pin": "SCL",
                "to_instance": "board",
                "to_pin": "D23",
                "signal_role": "scl",
            },
        ],
    )
    codes = {i.code for i in validate_i2c(spec, cat, board)}
    assert "I2C_BUS_SPLIT" in codes


def test_logic_levels_ignores_non_output_pins_and_rails():
    cat, board = _board()
    spec = _spec(
        parts=[
            {"instance_id": "board", "part_id": "esp32_devkit_30pin"},
            {"instance_id": "range", "part_id": "hc_sr04"},
        ],
        connections=[
            {
                "from_instance": "range",
                "from_pin": "VCC",
                "to_instance": "board",
                "to_pin": "5V",
                "signal_role": "rail",
                "rail_name": "5V",
            },
            {
                "from_instance": "range",
                "from_pin": "TRIG",
                "to_instance": "board",
                "to_pin": "D27",
                "signal_role": "signal",
            },
        ],
    )
    # TRIG is an input on the sensor, so it is not flagged; the rail is skipped.
    codes = {i.code for i in validate_logic_levels(spec, cat, board)}
    assert "LOGIC_LEVEL_HIGH" not in codes


def test_behaviors_no_action():
    from sparkplan.validators.phase2.behaviors import validate_behaviors

    cat, board = _board()
    spec = _spec(
        parts=[
            {"instance_id": "board", "part_id": "esp32_devkit_30pin"},
            {"instance_id": "env", "part_id": "dht22"},
        ],
        behaviors=[
            {
                "name": "b",
                "readings": [{"name": "temperature_c", "unit": "C"}],
                "rules": [
                    {
                        "name": "no_action",
                        "condition_reading": "temperature_c",
                        "condition_unit": "C",
                    }
                ],
                "display_lines": [
                    {"target_instance": "env", "reading": "temperature_c", "unit": "C"}
                ],
            }
        ],
    )
    codes = {i.code for i in validate_behaviors(spec, cat, board)}
    assert "BEHAVIOR_NO_ACTION" in codes


def test_behaviors_unknown_unit_on_rule():
    from sparkplan.validators.phase2.behaviors import validate_behaviors

    cat, board = _board()
    spec = _spec(
        parts=[
            {"instance_id": "board", "part_id": "esp32_devkit_30pin"},
            {"instance_id": "env", "part_id": "dht22"},
        ],
        behaviors=[
            {
                "name": "b",
                "readings": [{"name": "temperature_c", "unit": "C"}],
                "rules": [
                    {
                        "name": "r",
                        "condition_reading": "temperature_c",
                        "condition_unit": "F",
                        "action_reading": "temperature_c",
                    }
                ],
            }
        ],
    )
    codes = {i.code for i in validate_behaviors(spec, cat, board)}
    assert "BEHAVIOR_UNIT_MISMATCH" in codes


def test_run_all_reports_crash(tmp_path, monkeypatch):
    import sparkplan.validators.phase2.report as report_mod

    def boom(spec, catalog, board):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(
        report_mod,
        "_validator_registry",
        lambda: [("VX_boom", boom)],
    )
    spec = _spec()
    r = run_all(spec)
    assert "VALIDATOR_CRASH" in {i.code for i in r.issues}
