"""Shared BuildSpec fixtures used across stage test suites."""

from __future__ import annotations

from sparkplan.models.spec import BuildSpec, Issue


def make_spec(**overrides) -> BuildSpec:
    """Valid baseline: desk weather station (DHT22 + SSD1306 OLED)."""
    data = {
        "idea": "desk weather station showing temperature and humidity",
        "controller": "esp32_devkit_v4",
        "parts": [
            {"instance_id": "env", "part_id": "dht22"},
            {"instance_id": "screen", "part_id": "ssd1306_oled"},
        ],
        "connections": [
            {"instance_id": "env", "peripheral_pin": "DATA", "controller_pin": "GPIO4"},
            {"instance_id": "screen", "peripheral_pin": "SDA", "controller_pin": "GPIO21"},
            {"instance_id": "screen", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
        ],
        "actions": [
            {
                "action_id": "read_temp",
                "kind": "read",
                "instance_id": "env",
                "api": "read_temperature_c",
            },
            {
                "action_id": "read_hum",
                "kind": "read",
                "instance_id": "env",
                "api": "read_humidity_pct",
            },
            {
                "action_id": "show",
                "kind": "write",
                "instance_id": "screen",
                "api": "print_line1",
                "consumes": "read_temp",
                "params": {"format": "T={value:.1f}C"},
            },
        ],
    }
    data.update(overrides)
    return BuildSpec.model_validate(data)


def codes(issues: list[Issue]) -> set[str]:
    return {i.code for i in issues}


def errors(issues: list[Issue]) -> set[str]:
    return {i.code for i in issues if i.severity == "error"}
