from __future__ import annotations

from sparkplan.validators import run_validators
from tests.validators.conftest import codes, errors, make_spec


def test_unknown_api_is_error(catalog):
    spec = make_spec(
        actions=[
            {"action_id": "read_temp", "kind": "read", "instance_id": "env", "api": "read_mind"},
            {
                "action_id": "show",
                "kind": "write",
                "instance_id": "screen",
                "api": "print_line1",
                "consumes": "read_temp",
            },
        ]
    )
    assert "ACTION_UNKNOWN_API" in errors(run_validators(spec, catalog))


def test_kind_mismatch_is_error(catalog):
    spec = make_spec(
        actions=[
            {
                "action_id": "read_temp",
                "kind": "write",
                "instance_id": "env",
                "api": "read_temperature_c",
            },
            {
                "action_id": "show",
                "kind": "write",
                "instance_id": "screen",
                "api": "print_line1",
                "consumes": "read_temp",
            },
        ]
    )
    assert "ACTION_KIND_MISMATCH" in errors(run_validators(spec, catalog))


def test_consumes_unknown_is_error(catalog):
    spec = make_spec(
        actions=[
            {
                "action_id": "read_temp",
                "kind": "read",
                "instance_id": "env",
                "api": "read_temperature_c",
            },
            {
                "action_id": "show",
                "kind": "write",
                "instance_id": "screen",
                "api": "print_line1",
                "consumes": "read_nope",
            },
        ]
    )
    assert "ACTION_CONSUMES_UNKNOWN" in errors(run_validators(spec, catalog))


def test_consumes_write_is_error(catalog):
    spec = make_spec(
        parts=[
            {"instance_id": "env", "part_id": "dht22"},
            {"instance_id": "screen", "part_id": "ssd1306_oled"},
            {"instance_id": "buzzer", "part_id": "buzzer_active_module"},
        ],
        actions=[
            {
                "action_id": "beep",
                "kind": "write",
                "instance_id": "buzzer",
                "api": "beep",
                "params": {"on": True},
            },
            {
                "action_id": "show",
                "kind": "write",
                "instance_id": "screen",
                "api": "print_line1",
                "consumes": "beep",
            },
            {
                "action_id": "read_temp",
                "kind": "read",
                "instance_id": "env",
                "api": "read_temperature_c",
            },
        ],
    )
    assert "ACTION_CONSUMES_NOT_READ" in errors(run_validators(spec, catalog))


def test_action_cycle_is_error(catalog):
    spec = make_spec(
        actions=[
            {
                "action_id": "read_temp",
                "kind": "read",
                "instance_id": "env",
                "api": "read_temperature_c",
                "consumes": "show",
            },
            {
                "action_id": "show",
                "kind": "write",
                "instance_id": "screen",
                "api": "print_line1",
                "consumes": "read_temp",
            },
        ]
    )
    assert "ACTION_CYCLE" in errors(run_validators(spec, catalog))


def test_duplicate_action_id(catalog):
    spec = make_spec(
        actions=[
            {
                "action_id": "read_temp",
                "kind": "read",
                "instance_id": "env",
                "api": "read_temperature_c",
            },
            {
                "action_id": "read_temp",
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
            },
        ]
    )
    assert "DUPLICATE_ACTION_ID" in errors(run_validators(spec, catalog))


def test_action_unknown_instance(catalog):
    spec = make_spec(
        actions=[
            {
                "action_id": "read_temp",
                "kind": "read",
                "instance_id": "ghost",
                "api": "read_temperature_c",
            },
            {
                "action_id": "show",
                "kind": "write",
                "instance_id": "screen",
                "api": "print_line1",
                "consumes": "read_temp",
            },
        ]
    )
    assert "ACTION_UNKNOWN_INSTANCE" in errors(run_validators(spec, catalog))


def test_unused_part_warns(catalog):
    spec = make_spec(
        parts=[
            {"instance_id": "env", "part_id": "dht22"},
            {"instance_id": "screen", "part_id": "ssd1306_oled"},
            {"instance_id": "buzzer", "part_id": "buzzer_active_module"},
        ],
        connections=[
            {"instance_id": "env", "peripheral_pin": "DATA", "controller_pin": "GPIO4"},
            {"instance_id": "screen", "peripheral_pin": "SDA", "controller_pin": "GPIO21"},
            {"instance_id": "screen", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
            {"instance_id": "buzzer", "peripheral_pin": "I/O", "controller_pin": "GPIO13"},
        ],
        actions=[
            {
                "action_id": "read_temp",
                "kind": "read",
                "instance_id": "env",
                "api": "read_temperature_c",
            },
            {
                "action_id": "show",
                "kind": "write",
                "instance_id": "screen",
                "api": "print_line1",
                "consumes": "read_temp",
            },
        ],
    )
    issues = run_validators(spec, catalog)
    assert "UNUSED_PART" in codes(issues)
    assert "UNUSED_PART" not in errors(issues)
