"""Tests for the firmware template engine.

The engine turns a validated build plus the catalog templates into C++
fragments.  It must never invent a GPIO: an unresolved pin or an unknown part
is an issue, not a guess.
"""

from __future__ import annotations

import pytest
from sparkplan.firmware.template import (
    TemplateEngine,
    board_pin_to_gpio,
    canonical_hash,
    canonical_payload,
)
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


def _spec(parts: list[dict], connections: list[dict]) -> SupportedBuild:
    return SupportedBuild.model_validate(
        {
            "status": "ok",
            "title": "Test build",
            "summary": "A build used by the template tests.",
            "parts": [_board(), *parts],
            "connections": connections,
        }
    )


def test_board_pin_to_gpio():
    assert board_pin_to_gpio("D4") == 4
    assert board_pin_to_gpio("GPIO21") == 21
    assert board_pin_to_gpio("3V3") is None
    assert board_pin_to_gpio("GND") is None


def test_pins_come_from_validated_connections(catalog):
    spec = _spec(
        [{"instance_id": "env", "part_id": "dht22"}],
        [_connection("env", "DATA", "D4")],
    )
    rendered = TemplateEngine(spec, catalog).render()
    assert not rendered.errors
    instance = rendered.instances[0]
    assert [(b.pin_name, b.gpio, b.var) for b in instance.pins] == [("DATA", 4, "env_DATA_PIN")]
    assert "DHT env_dht(env_DATA_PIN, DHT22);" in instance.globals


def test_missing_connection_is_an_error(catalog):
    spec = _spec([{"instance_id": "env", "part_id": "dht22"}], [])
    rendered = TemplateEngine(spec, catalog).render()
    assert rendered.errors
    assert "FIRMWARE_UNRESOLVED_PLACEHOLDER" in {issue.code for issue in rendered.issues}


def test_led_anode_on_board_gpio_resolves(catalog):
    spec = _spec(
        [
            {"instance_id": "led", "part_id": "led_5mm"},
            {"instance_id": "res", "part_id": "resistor_220r"},
        ],
        [
            _connection("led", "ANODE", "D4"),
            {
                "from_instance": "led",
                "from_pin": "CATHODE",
                "to_instance": "res",
                "to_pin": "A",
                "signal_role": "passive",
            },
            _connection("res", "B", "GND", "gnd"),
        ],
    )
    rendered = TemplateEngine(spec, catalog).render()
    assert not rendered.errors
    led = next(instance for instance in rendered.instances if instance.instance_id == "led")
    assert [(binding.pin_name, binding.gpio, binding.var) for binding in led.pins] == [
        ("ANODE", 4, "led_LED_PIN")
    ]
    assert "pinMode(led_LED_PIN, OUTPUT);" in led.setup


def test_led_without_anode_connection_is_an_error(catalog):
    spec = _spec([{"instance_id": "led", "part_id": "led_5mm"}], [])
    rendered = TemplateEngine(spec, catalog).render()
    assert any(
        issue.code == "FIRMWARE_UNRESOLVED_PLACEHOLDER"
        and "no validated connection for pin 'ANODE'" in issue.message
        for issue in rendered.issues
    )


def test_rail_powered_led_anode_is_unresolved(catalog):
    spec = _spec(
        [
            {"instance_id": "led", "part_id": "led_5mm"},
            {"instance_id": "res", "part_id": "resistor_220r"},
        ],
        [
            {
                "from_instance": "res",
                "from_pin": "A",
                "to_instance": "board",
                "to_pin": "3V3",
                "signal_role": "rail",
                "rail_name": "3V3",
            },
            {
                "from_instance": "res",
                "from_pin": "B",
                "to_instance": "led",
                "to_pin": "ANODE",
                "signal_role": "signal",
            },
            _connection("led", "CATHODE", "GND", "gnd"),
        ],
    )
    rendered = TemplateEngine(spec, catalog).render()
    assert any(
        issue.code == "FIRMWARE_UNRESOLVED_PLACEHOLDER"
        and "no validated connection for pin 'ANODE'" in issue.message
        for issue in rendered.issues
    )


def test_gpio_assignments_are_reported(catalog):
    spec = _spec(
        [{"instance_id": "env", "part_id": "dht22"}],
        [_connection("env", "DATA", "D4")],
    )
    rendered = TemplateEngine(spec, catalog).render()
    assert [
        (a.instance_id, a.pin_name, a.board_pin, a.gpio) for a in rendered.gpio_assignments
    ] == [("env", "DATA", "D4", 4)]


def test_objects_are_scoped_per_instance(catalog):
    spec = _spec(
        [
            {"instance_id": "screen1", "part_id": "ssd1306_oled_096"},
            {"instance_id": "screen2", "part_id": "ssd1306_oled_096"},
        ],
        [
            _connection("screen1", "SDA", "D21", "sda"),
            _connection("screen1", "SCL", "D22", "scl"),
            _connection("screen2", "SDA", "D21", "sda"),
            _connection("screen2", "SCL", "D22", "scl"),
        ],
    )
    rendered = TemplateEngine(spec, catalog).render()
    assert not rendered.errors
    assert rendered.instances[0].object_var == "screen1_display"
    assert rendered.instances[1].object_var == "screen2_display"
    assert "Adafruit_SSD1306 screen1_display(128, 64, &Wire, -1);" in rendered.instances[0].globals


def test_member_names_are_not_substituted(catalog):
    spec = _spec(
        [{"instance_id": "screen", "part_id": "ssd1306_oled_096"}],
        [
            _connection("screen", "SDA", "D21", "sda"),
            _connection("screen", "SCL", "D22", "scl"),
        ],
    )
    rendered = TemplateEngine(spec, catalog).render()
    action = rendered.instances[0].actions[0]
    assert "screen_display.print(value);" in action.snippet
    assert "screen_display.screen_display" not in action.snippet


def test_i2c_begin_uses_the_validated_pins(catalog):
    spec = _spec(
        [{"instance_id": "screen", "part_id": "ssd1306_oled_096"}],
        [
            _connection("screen", "SDA", "D21", "sda"),
            _connection("screen", "SCL", "D22", "scl"),
        ],
    )
    rendered = TemplateEngine(spec, catalog).render()
    assert rendered.instances[0].i2c_begin == "Wire.begin(screen_SDA_PIN, screen_SCL_PIN);"


def test_board_setup_is_collected(catalog):
    spec = _spec(
        [{"instance_id": "env", "part_id": "dht22"}],
        [_connection("env", "DATA", "D4")],
    )
    rendered = TemplateEngine(spec, catalog).render()
    assert "Serial.begin(115200);" in rendered.board_setup


def test_libraries_come_from_the_catalog(catalog):
    spec = _spec(
        [{"instance_id": "env", "part_id": "dht22"}],
        [_connection("env", "DATA", "D4")],
    )
    rendered = TemplateEngine(spec, catalog).render()
    assert rendered.libraries == ("DHT sensor library", "Adafruit Unified Sensor")


def test_minimum_interval_comes_from_the_part(catalog):
    spec = _spec(
        [{"instance_id": "env", "part_id": "dht22"}],
        [_connection("env", "DATA", "D4")],
    )
    rendered = TemplateEngine(spec, catalog).render()
    assert rendered.instances[0].min_interval_ms == 2000


def test_canonical_hash_ignores_the_order_of_parts_and_connections(catalog):
    spec_a = _spec(
        [
            {"instance_id": "env", "part_id": "dht22"},
            {"instance_id": "screen", "part_id": "ssd1306_oled_096"},
        ],
        [
            _connection("env", "DATA", "D4"),
            _connection("screen", "SDA", "D21", "sda"),
            _connection("screen", "SCL", "D22", "scl"),
        ],
    )
    spec_b = _spec(
        [
            {"instance_id": "screen", "part_id": "ssd1306_oled_096"},
            {"instance_id": "env", "part_id": "dht22"},
        ],
        [
            _connection("screen", "SCL", "D22", "scl"),
            _connection("screen", "SDA", "D21", "sda"),
            _connection("env", "DATA", "D4"),
        ],
    )
    assert canonical_hash(canonical_payload(spec_a)) == canonical_hash(canonical_payload(spec_b))


def test_canonical_hash_changes_with_the_content(catalog):
    spec_a = _spec(
        [{"instance_id": "env", "part_id": "dht22"}],
        [_connection("env", "DATA", "D4")],
    )
    spec_b = _spec(
        [{"instance_id": "env", "part_id": "dht22"}],
        [_connection("env", "DATA", "D5")],
    )
    assert canonical_hash(canonical_payload(spec_a)) != canonical_hash(canonical_payload(spec_b))
