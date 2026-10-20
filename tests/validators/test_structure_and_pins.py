from __future__ import annotations

from sparkplan.validators import run_validators
from tests.validators.conftest import codes, errors, make_spec


def test_baseline_is_clean(catalog):
    issues = run_validators(make_spec(), catalog)
    assert errors(issues) == set()


def test_no_parts_is_error(catalog):
    spec = make_spec(parts=[], connections=[], actions=[])
    assert "NO_PARTS" in errors(run_validators(spec, catalog))


def test_unknown_part_is_error(catalog):
    spec = make_spec(
        parts=[{"instance_id": "ghost", "part_id": "camera_module_v2"}],
        connections=[],
        actions=[],
    )
    assert "UNKNOWN_PART" in errors(run_validators(spec, catalog))


def test_duplicate_instance_is_error(catalog):
    spec = make_spec(
        parts=[
            {"instance_id": "env", "part_id": "dht22"},
            {"instance_id": "env", "part_id": "bmp280"},
        ]
    )
    assert "DUPLICATE_INSTANCE" in errors(run_validators(spec, catalog))


def test_unknown_controller_pin(catalog):
    spec = make_spec(
        connections=[
            {"instance_id": "env", "peripheral_pin": "DATA", "controller_pin": "GPIO99"},
            {"instance_id": "screen", "peripheral_pin": "SDA", "controller_pin": "GPIO21"},
            {"instance_id": "screen", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
        ]
    )
    assert "CONTROLLER_PIN_MISSING" in errors(run_validators(spec, catalog))


def test_gpio_conflict(catalog):
    spec = make_spec(
        connections=[
            {"instance_id": "env", "peripheral_pin": "DATA", "controller_pin": "GPIO4"},
            {"instance_id": "screen", "peripheral_pin": "SDA", "controller_pin": "GPIO4"},
            {"instance_id": "screen", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
        ]
    )
    assert "GPIO_CONFLICT" in errors(run_validators(spec, catalog))


def test_gpio_reserved_uart0(catalog):
    spec = make_spec(
        connections=[
            {"instance_id": "env", "peripheral_pin": "DATA", "controller_pin": "GPIO1"},
            {"instance_id": "screen", "peripheral_pin": "SDA", "controller_pin": "GPIO21"},
            {"instance_id": "screen", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
        ]
    )
    assert "GPIO_RESERVED" in errors(run_validators(spec, catalog))


def test_input_only_gpio_rejects_output(catalog):
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
            {"instance_id": "buzzer", "peripheral_pin": "I/O", "controller_pin": "GPIO34"},
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
            {
                "action_id": "beep",
                "kind": "write",
                "instance_id": "buzzer",
                "api": "beep",
                "params": {"on": True},
            },
        ],
    )
    assert "GPIO_INPUT_ONLY" in errors(run_validators(spec, catalog))


def test_connection_to_power_pin_is_error(catalog):
    spec = make_spec(
        connections=[
            {"instance_id": "env", "peripheral_pin": "VCC", "controller_pin": "GPIO4"},
            {"instance_id": "screen", "peripheral_pin": "SDA", "controller_pin": "GPIO21"},
            {"instance_id": "screen", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
        ]
    )
    assert "CONNECTION_POWER_PIN" in errors(run_validators(spec, catalog))


def test_unknown_peripheral_pin(catalog):
    spec = make_spec(
        connections=[
            {"instance_id": "env", "peripheral_pin": "DATA2", "controller_pin": "GPIO4"},
            {"instance_id": "screen", "peripheral_pin": "SDA", "controller_pin": "GPIO21"},
            {"instance_id": "screen", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
        ]
    )
    assert "CONNECTION_UNKNOWN_PIN" in errors(run_validators(spec, catalog))


def test_i2c_pin_unconnected(catalog):
    spec = make_spec(
        connections=[
            {"instance_id": "env", "peripheral_pin": "DATA", "controller_pin": "GPIO4"},
        ]
    )
    got = errors(run_validators(spec, catalog))
    assert "I2C_PIN_UNCONNECTED" in got


def test_i2c_bus_split(catalog):
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
            {"instance_id": "baro", "peripheral_pin": "SDA", "controller_pin": "GPIO17"},
            {"instance_id": "baro", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
        ],
    )
    assert "I2C_BUS_SPLIT" in errors(run_validators(spec, catalog))


def test_i2c_address_conflict(catalog):
    spec = make_spec(
        parts=[
            {"instance_id": "env", "part_id": "dht22"},
            {"instance_id": "screen", "part_id": "ssd1306_oled"},
            {"instance_id": "baro", "part_id": "bmp280"},
            {"instance_id": "baro2", "part_id": "bmp280"},
        ],
        connections=[
            {"instance_id": "env", "peripheral_pin": "DATA", "controller_pin": "GPIO4"},
            {"instance_id": "screen", "peripheral_pin": "SDA", "controller_pin": "GPIO21"},
            {"instance_id": "screen", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
            {"instance_id": "baro", "peripheral_pin": "SDA", "controller_pin": "GPIO21"},
            {"instance_id": "baro", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
            {"instance_id": "baro2", "peripheral_pin": "SDA", "controller_pin": "GPIO21"},
            {"instance_id": "baro2", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
        ],
    )
    assert "I2C_ADDRESS_CONFLICT" in errors(run_validators(spec, catalog))


def test_level_shift_warning_for_5v_echo(catalog):
    spec = make_spec(
        parts=[
            {"instance_id": "env", "part_id": "dht22"},
            {"instance_id": "screen", "part_id": "ssd1306_oled"},
            {"instance_id": "range", "part_id": "hc_sr04"},
        ],
        connections=[
            {"instance_id": "env", "peripheral_pin": "DATA", "controller_pin": "GPIO4"},
            {"instance_id": "screen", "peripheral_pin": "SDA", "controller_pin": "GPIO21"},
            {"instance_id": "screen", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
            {"instance_id": "range", "peripheral_pin": "TRIG", "controller_pin": "GPIO13"},
            {"instance_id": "range", "peripheral_pin": "ECHO", "controller_pin": "GPIO34"},
        ],
    )
    issues = run_validators(spec, catalog)
    assert "LEVEL_SHIFT_REQUIRED" in codes(issues)
    assert "GPIO_INPUT_ONLY" not in errors(issues)


def test_multi_gpio_incomplete(catalog):
    spec = make_spec(
        parts=[
            {"instance_id": "env", "part_id": "dht22"},
            {"instance_id": "screen", "part_id": "ssd1306_oled"},
            {"instance_id": "range", "part_id": "hc_sr04"},
        ],
        connections=[
            {"instance_id": "env", "peripheral_pin": "DATA", "controller_pin": "GPIO4"},
            {"instance_id": "screen", "peripheral_pin": "SDA", "controller_pin": "GPIO21"},
            {"instance_id": "screen", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
            {"instance_id": "range", "peripheral_pin": "TRIG", "controller_pin": "GPIO13"},
        ],
    )
    assert "MULTI_GPIO_INCOMPLETE" in errors(run_validators(spec, catalog))


def test_strap_gpio_warns(catalog):
    spec = make_spec(
        connections=[
            {"instance_id": "env", "peripheral_pin": "DATA", "controller_pin": "GPIO0"},
            {"instance_id": "screen", "peripheral_pin": "SDA", "controller_pin": "GPIO21"},
            {"instance_id": "screen", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
        ]
    )
    assert "GPIO_STRAP" in codes(run_validators(spec, catalog))


def test_analog_sensor_needs_adc(catalog):
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
            {"instance_id": "light", "peripheral_pin": "AO", "controller_pin": "GPIO17"},
        ],
    )
    assert "GPIO_NOT_ADC" in errors(run_validators(spec, catalog))
