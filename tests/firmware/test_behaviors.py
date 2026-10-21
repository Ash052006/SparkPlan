"""Tests for the behaviour based firmware generator.

The generator must render a build without inventing hardware semantics:
unknown readings and unsupported rule actions are reported, readings stay
scoped to their instance, and the same build always renders the same bytes.
"""

from __future__ import annotations

import pytest
from sparkplan.firmware import generate_behavior_firmware
from sparkplan.firmware.errors import FirmwareError
from sparkplan.firmware.generate import generate_firmware
from sparkplan.models.buildspec import SupportedBuild
from sparkplan.models.catalog import Catalog, load_catalog


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog()


def _board() -> dict:
    return {"instance_id": "board", "part_id": "esp32_devkit_30pin"}


def _connection(instance: str, pin: str, board_pin: str, role: str = "signal") -> dict:
    return {
        "from_instance": instance,
        "from_pin": pin,
        "to_instance": "board",
        "to_pin": board_pin,
        "signal_role": role,
    }


def _build(**overrides) -> SupportedBuild:
    payload = {
        "status": "ok",
        "title": "Soil monitor",
        "summary": "Shows the soil moisture and the air temperature.",
        "parts": [
            _board(),
            {"instance_id": "soil", "part_id": "soil_moisture_capacitive"},
            {"instance_id": "env", "part_id": "dht22"},
            {"instance_id": "screen", "part_id": "ssd1306_oled_096"},
        ],
        "connections": [
            _connection("soil", "AOUT", "D32"),
            _connection("env", "DATA", "D4"),
            _connection("screen", "SDA", "D21", "sda"),
            _connection("screen", "SCL", "D22", "scl"),
        ],
        "behaviors": [
            {
                "name": "monitor",
                "part_ids": ["soil_moisture_capacitive", "dht22"],
                "readings": [{"name": "soil_raw", "unit": "adc_counts", "instance_id": "soil"}],
                "rules": [
                    {
                        "name": "dry_alert",
                        "condition_reading": "soil_raw",
                        "condition_unit": "adc_counts",
                        "comparator": ">",
                        "threshold": 3000,
                        "action_reading": "soil_raw",
                    }
                ],
                "display_lines": [
                    {
                        "target_instance": "screen",
                        "reading": "temperature_c",
                        "unit": "C",
                        "text": "Temp",
                    }
                ],
            }
        ],
    }
    payload.update(overrides)
    return SupportedBuild.model_validate(payload)


def test_readings_are_instance_scoped(catalog):
    result = generate_behavior_firmware(_build(), catalog)
    assert result.supported
    assert "float env_temperature_c = NAN;" in result.sketch
    assert "bool env_temperature_c_valid = false;" in result.sketch
    assert "int soil_soil_raw = 0;" in result.sketch
    assert "bool soil_soil_raw_valid = false;" in result.sketch
    assert result.readings["temperature_c"] == "env_temperature_c"
    assert result.readings["soil_raw"] == "soil_soil_raw"


def test_float_reading_guards_against_nan(catalog):
    sketch = generate_behavior_firmware(_build(), catalog).sketch
    assert "if (!isnan(t)) { env_temperature_c = t; env_temperature_c_valid = true; }" in sketch
    assert "else { env_temperature_c_valid = false; }" in sketch


def test_rule_is_edge_triggered(catalog):
    sketch = generate_behavior_firmware(_build(), catalog).sketch
    assert "static bool rule_dry_alert_active = false;" in sketch
    assert "const bool rule_dry_alert_now = soil_soil_raw_valid" in sketch
    assert "if (rule_dry_alert_now && !rule_dry_alert_active) {" in sketch
    assert "rule_dry_alert_active = rule_dry_alert_now;" in sketch


def test_rule_fires_only_on_valid_readings(catalog):
    sketch = generate_behavior_firmware(_build(), catalog).sketch
    assert "soil_soil_raw_valid &&" in sketch
    assert "(double)soil_soil_raw > 3000.0" in sketch


def test_display_line_uses_one_buffer_per_target(catalog):
    sketch = generate_behavior_firmware(_build(), catalog).sketch
    assert "char line_screen[160];" in sketch
    assert sketch.count('Serial.printf("%s", line_screen);') == 1
    assert "screen_display.print(line_screen);" in sketch
    assert "screen_display.screen_display" not in sketch


def test_display_line_shows_na_when_the_reading_is_invalid(catalog):
    sketch = generate_behavior_firmware(_build(), catalog).sketch
    assert (
        "snprintf(cursor, sizeof(line_screen) - (size_t)(cursor - line_screen), "
        '"Temp: %.2f C\\n"'
    ) in sketch
    assert '"Temp: n/a\\n"' in sketch


def test_slow_part_gets_its_own_schedule(catalog):
    sketch = generate_behavior_firmware(_build(), catalog).sketch
    assert "const uint32_t SPARKPLAN_INTERVAL_env_MS = 2000;" in sketch
    assert "static uint32_t last_read_env_ms = 0;" in sketch
    assert "if ((uint32_t)(now - last_read_env_ms) >= SPARKPLAN_INTERVAL_env_MS) {" in sketch
    # The fast part keeps the plain tick schedule.
    assert "read_soil();" in sketch


def test_render_is_deterministic(catalog):
    first = generate_behavior_firmware(_build(), catalog)
    second = generate_behavior_firmware(_build(), catalog)
    assert first.sketch == second.sketch
    assert "Spec hash (sha256/16):" in first.sketch


def test_actuator_rule_is_reported_as_unsupported(catalog):
    spec = _build()
    spec.behaviors[0].rules[0].action_reading = ""
    spec.behaviors[0].rules[0].action_actuator = "servo"
    result = generate_behavior_firmware(spec, catalog)
    assert not result.supported
    assert "FIRMWARE_UNSUPPORTED_RULE_ACTION" in {issue.code for issue in result.issues}
    assert result.sketch == ""


def test_unknown_reading_is_reported(catalog):
    spec = _build()
    spec.behaviors[0].display_lines[0].reading = "pressure_hpa"
    result = generate_behavior_firmware(spec, catalog)
    assert not result.supported
    assert "FIRMWARE_UNKNOWN_READING" in {issue.code for issue in result.issues}


def test_unit_mismatch_is_reported(catalog):
    spec = _build()
    spec.behaviors[0].display_lines[0].unit = "F"
    result = generate_behavior_firmware(spec, catalog)
    assert not result.supported
    assert "FIRMWARE_UNIT_MISMATCH" in {issue.code for issue in result.issues}


def test_generate_firmware_dispatches_supported_builds(catalog):
    result = generate_firmware(_build(), catalog)
    assert result.readings["temperature_c"] == "env_temperature_c"
    assert "DHT sensor library" in result.libraries
    assert "Adafruit SSD1306" in result.libraries


def test_generate_firmware_rejects_the_wrong_catalog(catalog):
    from sparkplan.catalog import load_catalog as load_legacy_catalog

    with pytest.raises(FirmwareError):
        generate_firmware(_build(), load_legacy_catalog())


def test_the_shared_i2c_bus_is_brought_up_once(catalog):
    spec = SupportedBuild.model_validate(
        {
            "status": "ok",
            "title": "Two I2C devices",
            "summary": "A BME280 and an OLED on the same bus.",
            "parts": [
                _board(),
                {"instance_id": "weather", "part_id": "bme280"},
                {"instance_id": "screen", "part_id": "ssd1306_oled_096"},
            ],
            "connections": [
                _connection("weather", "SDA", "D21", "sda"),
                _connection("weather", "SCL", "D22", "scl"),
                _connection("screen", "SDA", "D21", "sda"),
                _connection("screen", "SCL", "D22", "scl"),
            ],
        }
    )
    sketch = generate_behavior_firmware(spec, catalog).sketch
    assert sketch.count("Wire.begin(") == 1
