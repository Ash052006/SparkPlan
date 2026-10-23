from __future__ import annotations

import os
import stat
import textwrap

import pytest
from sparkplan.catalog import load_catalog
from sparkplan.firmware import (
    FirmwareError,
    compile_firmware,
    generate_firmware,
    write_sketch,
)
from tests.buildspecs import make_spec


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def test_weather_station_sketch(catalog):
    result = generate_firmware(make_spec(), catalog)
    sketch = result.sketch
    assert "#include <DHT.h>" in sketch
    assert "Adafruit_SSD1306" in sketch
    # The ESP32 core has no "GPIO4" symbol, so the catalog pin label
    # "GPIO4" is emitted as the GPIO number the label stands for.
    assert "DHT env_dht(4, DHTTYPE);" in sketch
    assert "auto v_read_temp = env_read_temperature_c();" in sketch
    assert "snprintf(v_show" in sketch
    assert "screen_print_line1(v_show);" in sketch
    assert not any(
        tok in sketch for tok in ("{instance}", "{gpio}", "{gpio_")
    ), "unresolved placeholders"
    assert result.libraries == [
        "DHT sensor library",
        "Adafruit Unified Sensor",
        "Adafruit GFX Library",
        "Adafruit SSD1306",
    ]


def test_multi_gpio_substitution(catalog):
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
        actions=[
            {
                "action_id": "read_temp",
                "kind": "read",
                "instance_id": "env",
                "api": "read_temperature_c",
            },
            {
                "action_id": "read_dist",
                "kind": "read",
                "instance_id": "range",
                "api": "read_distance_cm",
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
    sketch = generate_firmware(spec, catalog).sketch
    assert "pinMode(13, OUTPUT);" in sketch
    assert "pulseIn(34, HIGH" in sketch
    assert "auto v_read_dist = range_read_distance_cm();" in sketch


def test_servo_consumes_numeric_read(catalog):
    spec = make_spec(
        parts=[
            {"instance_id": "env", "part_id": "dht22"},
            {"instance_id": "screen", "part_id": "ssd1306_oled"},
            {"instance_id": "arm", "part_id": "sg90_servo"},
            {"instance_id": "cell", "part_id": "battery_holder_18650"},
            {"instance_id": "boost", "part_id": "mt3608_boost"},
        ],
        connections=[
            {"instance_id": "env", "peripheral_pin": "DATA", "controller_pin": "GPIO4"},
            {"instance_id": "screen", "peripheral_pin": "SDA", "controller_pin": "GPIO21"},
            {"instance_id": "screen", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
            {"instance_id": "arm", "peripheral_pin": "SIG", "controller_pin": "GPIO13"},
        ],
        actions=[
            {
                "action_id": "read_temp",
                "kind": "read",
                "instance_id": "env",
                "api": "read_temperature_c",
            },
            {
                "action_id": "move",
                "kind": "write",
                "instance_id": "arm",
                "api": "write_angle",
                "consumes": "read_temp",
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
    sketch = generate_firmware(spec, catalog).sketch
    assert "arm_write_angle((float)v_read_temp);" in sketch
    assert "ESP32Servo.h" in sketch


def test_literal_params_rendered(catalog):
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
            {
                "action_id": "beep",
                "kind": "write",
                "instance_id": "buzzer",
                "api": "beep",
                "params": {"on": True},
            },
        ],
    )
    sketch = generate_firmware(spec, catalog).sketch
    assert "buzzer_beep(true);" in sketch


def test_unresolved_placeholder_raises(catalog):
    # DHT22 needs exactly one GPIO; connect it to nothing and generation
    # must fail loudly rather than emit broken C++.
    spec = make_spec(
        parts=[{"instance_id": "env", "part_id": "dht22"}],
        connections=[],
        actions=[
            {
                "action_id": "read_temp",
                "kind": "read",
                "instance_id": "env",
                "api": "read_temperature_c",
            }
        ],
    )
    with pytest.raises(FirmwareError):
        generate_firmware(spec, catalog)


def test_write_sketch_and_compile_skipped(catalog, tmp_path):
    result = generate_firmware(make_spec(), catalog)
    path = write_sketch(result, tmp_path / "sparkplan_ino")
    assert path.name == "sparkplan.ino"
    assert path.read_text() == result.sketch

    env_backup = os.environ.pop("SPARKPLAN_ARDUINO_CLI", None)
    try:
        compiled = compile_firmware(result, tmp_path / "sparkplan_ino", cli_path=None)
        assert compiled.compile_status in ("skipped", "ok", "failed")
        if compiled.compile_status == "skipped":
            assert "arduino-cli" in compiled.compile_log
    finally:
        if env_backup is not None:
            os.environ["SPARKPLAN_ARDUINO_CLI"] = env_backup


def test_compile_with_fake_cli(catalog, tmp_path):
    fake_cli = tmp_path / "arduino-cli"
    fake_cli.write_text(textwrap.dedent("#!/bin/sh\necho fake-compile $@\nexit 0\n"))
    fake_cli.chmod(fake_cli.stat().st_mode | stat.S_IEXEC)
    result = generate_firmware(make_spec(), catalog)
    compiled = compile_firmware(result, tmp_path / "ino", cli_path=str(fake_cli))
    assert compiled.compile_status == "ok"
    assert "fake-compile" in compiled.compile_log


def test_compile_failure_reported(catalog, tmp_path):
    fake_cli = tmp_path / "arduino-cli"
    fake_cli.write_text(textwrap.dedent("#!/bin/sh\necho boom >&2\nexit 1\n"))
    fake_cli.chmod(fake_cli.stat().st_mode | stat.S_IEXEC)
    result = generate_firmware(make_spec(), catalog)
    compiled = compile_firmware(result, tmp_path / "ino", cli_path=str(fake_cli))
    assert compiled.compile_status == "failed"
    assert "boom" in compiled.compile_log


def test_write_sketch_accepts_an_explicit_safe_filename(catalog, tmp_path):
    result = generate_firmware(make_spec(), catalog)
    path = write_sketch(result, tmp_path / "custom", filename="sketch.ino")
    assert path == tmp_path / "custom" / "sketch.ino"
    assert path.read_text() == result.sketch


@pytest.mark.parametrize("name", ["../escape.ino", "nested/sketch.ino", "sketch.txt", ".ino"])
def test_write_sketch_rejects_unsafe_or_wrong_filenames(catalog, tmp_path, name):
    result = generate_firmware(make_spec(), catalog)
    with pytest.raises(FirmwareError):
        write_sketch(result, tmp_path / "custom", filename=name)
