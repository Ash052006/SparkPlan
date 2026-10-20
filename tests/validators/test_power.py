from __future__ import annotations

from sparkplan.models.spec import BuildSpec
from sparkplan.validators import run_validators
from sparkplan.validators.base import issues_to_machine
from tests.validators.conftest import codes, errors, make_spec


def _servo_spec(with_boost: bool = False) -> BuildSpec:
    parts = [
        {"instance_id": "knob", "part_id": "ky040_rotary_encoder"},
        {"instance_id": "arm", "part_id": "sg90_servo"},
    ]
    connections = [
        {"instance_id": "knob", "peripheral_pin": "CLK", "controller_pin": "GPIO17"},
        {"instance_id": "knob", "peripheral_pin": "DT", "controller_pin": "GPIO16"},
        {"instance_id": "knob", "peripheral_pin": "SW", "controller_pin": "GPIO15"},
        {"instance_id": "arm", "peripheral_pin": "SIG", "controller_pin": "GPIO13"},
    ]
    actions = [
        {"action_id": "delta", "kind": "read", "instance_id": "knob", "api": "read_delta"},
        {
            "action_id": "move",
            "kind": "write",
            "instance_id": "arm",
            "api": "write_angle",
            "consumes": "delta",
        },
    ]
    if with_boost:
        parts += [
            {"instance_id": "cell", "part_id": "battery_holder_18650"},
            {"instance_id": "boost", "part_id": "mt3608_boost"},
        ]
    return BuildSpec.model_validate(
        {
            "idea": "servo turned by a knob",
            "parts": parts,
            "connections": connections,
            "actions": actions,
        }
    )


def test_servo_over_usb_budget_fails(catalog):
    issues = run_validators(_servo_spec(with_boost=False), catalog)
    assert "POWER_BUDGET_5V" in errors(issues)


def test_servo_with_boost_and_battery_passes(catalog):
    issues = run_validators(_servo_spec(with_boost=True), catalog)
    assert "POWER_BUDGET_5V" not in errors(issues)
    assert "BOOST_WITHOUT_BATTERY" not in errors(issues)


def test_boost_without_battery_is_error(catalog):
    from sparkplan.models.spec import PartInstance

    spec = _servo_spec(with_boost=False)
    spec.parts.append(PartInstance(instance_id="boost", part_id="mt3608_boost"))
    assert "BOOST_WITHOUT_BATTERY" in errors(run_validators(spec, catalog))


def test_battery_without_boost_warns(catalog):
    spec = make_spec(
        parts=[
            {"instance_id": "env", "part_id": "dht22"},
            {"instance_id": "screen", "part_id": "ssd1306_oled"},
            {"instance_id": "cell", "part_id": "battery_holder_18650"},
        ],
    )
    assert "BATTERY_WITHOUT_BOOST" in codes(run_validators(spec, catalog))


def test_voltage_incompatible_part_is_error(catalog):
    # Mutate a copy of bmp280 so it claims the 5v rail while capping at 3.6 V.
    baro = catalog.part("bmp280").model_copy(deep=True)
    baro.supply.rail = "5v"
    parts = dict(catalog.parts)
    parts["bmp280"] = baro
    from sparkplan.catalog import Catalog

    custom = Catalog(
        parts=parts, power=catalog.power, gpios=catalog.gpios, capabilities=catalog.capabilities
    )
    spec = make_spec(
        parts=[
            {"instance_id": "env", "part_id": "dht22"},
            {"instance_id": "screen", "part_id": "ssd1306_oled"},
            {"instance_id": "baro", "part_id": "bmp280"},
        ],
        connections=[
            {"instance_id": "env", "peripheral_pin": "DATA", "controller_pin": "GPIO4"},
            {"instance_id": "screen", "peripheral_pin": "SDA", "controller_pin": "GPIO21"},
            {"instance_id": "screen", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
            {"instance_id": "baro", "peripheral_pin": "SDA", "controller_pin": "GPIO21"},
            {"instance_id": "baro", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
        ],
    )
    assert "VOLTAGE_INCOMPATIBLE" in errors(run_validators(spec, custom))


def test_analog_on_5v_rail_warns(catalog):
    parts = dict(catalog.parts)
    light = parts["photoresistor_module"].model_copy(deep=True)
    light.supply.rail = "5v"
    light.supply.voltage_max = 5.0
    parts["photoresistor_module"] = light
    from sparkplan.catalog import Catalog

    custom = Catalog(
        parts=parts, power=catalog.power, gpios=catalog.gpios, capabilities=catalog.capabilities
    )
    spec = make_spec(
        parts=[
            {"instance_id": "env", "part_id": "dht22"},
            {"instance_id": "screen", "part_id": "ssd1306_oled"},
            {"instance_id": "light", "part_id": "photoresistor_module"},
        ],
        connections=[
            {"instance_id": "env", "peripheral_pin": "DATA", "controller_pin": "GPIO4"},
            {"instance_id": "screen", "peripheral_pin": "SDA", "controller_pin": "GPIO21"},
            {"instance_id": "screen", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
            {"instance_id": "light", "peripheral_pin": "AO", "controller_pin": "GPIO32"},
        ],
        actions=[
            {
                "action_id": "read_temp",
                "kind": "read",
                "instance_id": "env",
                "api": "read_temperature_c",
            },
            {"action_id": "read_light", "kind": "read", "instance_id": "light", "api": "read_raw"},
            {
                "action_id": "show",
                "kind": "write",
                "instance_id": "screen",
                "api": "print_line1",
                "consumes": "read_temp",
            },
        ],
    )
    assert "ANALOG_OVERVOLTAGE" in codes(run_validators(spec, custom))


def test_power_budget_summary_info(catalog):
    issues = run_validators(make_spec(), catalog)
    assert "POWER_BUDGET_SUMMARY" in codes(issues)
    machine = issues_to_machine(issues)
    assert all({"code", "severity", "message", "part_ids", "fix_hint"} <= set(m) for m in machine)
