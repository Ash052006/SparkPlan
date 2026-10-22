"""Property-based tests for Phase 2 validator invariants.

These tests use Hypothesis to vary wiring details while checking the same
validator entry points and issue codes used by the example-based tests.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
from sparkplan.models.buildspec import (
    Connection,
    LayoutHint,
    PartRef,
    SupportedBuild,
)
from sparkplan.models.catalog import get_catalog
from sparkplan.validators.phase2 import run_all

CATALOG = get_catalog()
BOARD = CATALOG.board()
BOARD_MODEL = BOARD.board
ADC_GPIOS = [f"D{pin.gpio}" for pin in BOARD_MODEL.gpio if pin.caps.adc and not pin.strapping]
OUTPUT_GPIOS = [
    f"D{pin.gpio}"
    for pin in BOARD_MODEL.gpio
    if pin.caps.output and not pin.forbidden and not pin.strapping
]
FLASH_GPIOS = [f"D{gpio}" for gpio in range(6, 12)]
ALL_GPIOS = [f"D{pin.gpio}" for pin in BOARD_MODEL.gpio]
INSTANCE_IDS = st.from_regex(r"[a-z][a-z0-9_]{1,8}", fullmatch=True)


def _error_codes(report):
    return {issue.code for issue in report.errors}


def _soil_spec(
    board_id: str,
    soil_id: str,
    env_id: str,
    screen_id: str,
    soil_gpio: str,
    env_gpio: str,
    mount: str,
) -> SupportedBuild:
    return SupportedBuild(
        status="ok",
        title="Soil moisture monitor",
        summary="Reads soil moisture and temperature, shows them on an OLED.",
        parts=[
            PartRef(instance_id=board_id, part_id="esp32_devkit_30pin"),
            PartRef(instance_id=soil_id, part_id="soil_moisture_capacitive"),
            PartRef(instance_id=env_id, part_id="dht22"),
            PartRef(instance_id=screen_id, part_id="ssd1306_oled_096"),
        ],
        connections=[
            Connection(
                from_instance=soil_id,
                from_pin="VCC",
                to_instance=board_id,
                to_pin="3V3",
                signal_role="rail",
                rail_name="3V3",
            ),
            Connection(
                from_instance=soil_id,
                from_pin="AOUT",
                to_instance=board_id,
                to_pin=soil_gpio,
                signal_role="signal",
            ),
            Connection(
                from_instance=soil_id,
                from_pin="GND",
                to_instance=board_id,
                to_pin="GND",
                signal_role="gnd",
            ),
            Connection(
                from_instance=env_id,
                from_pin="VCC",
                to_instance=board_id,
                to_pin="3V3",
                signal_role="rail",
                rail_name="3V3",
            ),
            Connection(
                from_instance=env_id,
                from_pin="DATA",
                to_instance=board_id,
                to_pin=env_gpio,
                signal_role="signal",
            ),
            Connection(
                from_instance=env_id,
                from_pin="GND",
                to_instance=board_id,
                to_pin="GND",
                signal_role="gnd",
            ),
            Connection(
                from_instance=screen_id,
                from_pin="VCC",
                to_instance=board_id,
                to_pin="3V3",
                signal_role="rail",
                rail_name="3V3",
            ),
            Connection(
                from_instance=screen_id,
                from_pin="GND",
                to_instance=board_id,
                to_pin="GND",
                signal_role="gnd",
            ),
            Connection(
                from_instance=screen_id,
                from_pin="SDA",
                to_instance=board_id,
                to_pin="D21",
                signal_role="sda",
            ),
            Connection(
                from_instance=screen_id,
                from_pin="SCL",
                to_instance=board_id,
                to_pin="D22",
                signal_role="scl",
            ),
        ],
        layout=[
            LayoutHint(part_id="soil_moisture_capacitive", mount=mount),
            LayoutHint(part_id="dht22", mount=mount),
            LayoutHint(part_id="ssd1306_oled_096", mount=mount),
        ],
        behaviors=[],
    )


@settings(max_examples=50, database=None, deadline=None)
@given(gpio=st.sampled_from(ALL_GPIOS))
def test_direct_hc_sr04_echo_is_rejected(gpio):
    spec = SupportedBuild(
        status="ok",
        title="Ultrasonic sensor",
        summary="HC-SR04 with a direct echo connection.",
        parts=[
            PartRef(instance_id="board", part_id="esp32_devkit_30pin"),
            PartRef(instance_id="range", part_id="hc_sr04"),
        ],
        connections=[
            Connection(
                from_instance="range",
                from_pin="VCC",
                to_instance="board",
                to_pin="5V",
                signal_role="rail",
                rail_name="5V",
            ),
            Connection(
                from_instance="range",
                from_pin="GND",
                to_instance="board",
                to_pin="GND",
                signal_role="gnd",
            ),
            Connection(
                from_instance="range",
                from_pin="TRIG",
                to_instance="board",
                to_pin="D27",
                signal_role="signal",
            ),
            Connection(
                from_instance="range",
                from_pin="ECHO",
                to_instance="board",
                to_pin=gpio,
                signal_role="signal",
            ),
        ],
        layout=[LayoutHint(part_id="hc_sr04", mount="wall")],
        behaviors=[],
    )
    report = run_all(spec, CATALOG)
    assert "LOGIC_LEVEL_HIGH" in _error_codes(report)


@settings(max_examples=50, database=None, deadline=None)
@given(gpio=st.sampled_from(OUTPUT_GPIOS))
def test_divider_path_avoids_logic_level_error(gpio):
    spec = SupportedBuild(
        status="ok",
        title="Ultrasonic sensor",
        summary="HC-SR04 with a level-converted echo connection.",
        parts=[
            PartRef(instance_id="board", part_id="esp32_devkit_30pin"),
            PartRef(instance_id="range", part_id="hc_sr04"),
            PartRef(instance_id="div", part_id="voltage_divider_5v_3v3"),
        ],
        connections=[
            Connection(
                from_instance="range",
                from_pin="VCC",
                to_instance="board",
                to_pin="5V",
                signal_role="rail",
                rail_name="5V",
            ),
            Connection(
                from_instance="range",
                from_pin="GND",
                to_instance="board",
                to_pin="GND",
                signal_role="gnd",
            ),
            Connection(
                from_instance="range",
                from_pin="TRIG",
                to_instance="board",
                to_pin="D27",
                signal_role="signal",
            ),
            Connection(
                from_instance="range",
                from_pin="ECHO",
                to_instance="div",
                to_pin="VIN",
                signal_role="signal",
            ),
            Connection(
                from_instance="div",
                from_pin="VCC",
                to_instance="board",
                to_pin="5V",
                signal_role="rail",
                rail_name="5V",
            ),
            Connection(
                from_instance="div",
                from_pin="GND",
                to_instance="board",
                to_pin="GND",
                signal_role="gnd",
            ),
            Connection(
                from_instance="div",
                from_pin="VOUT",
                to_instance="board",
                to_pin=gpio,
                signal_role="signal",
            ),
        ],
        layout=[
            LayoutHint(part_id="hc_sr04", mount="wall"),
            LayoutHint(part_id="voltage_divider_5v_3v3", mount="base"),
        ],
        behaviors=[],
    )
    report = run_all(spec, CATALOG)
    assert "LOGIC_LEVEL_HIGH" not in _error_codes(report)


@settings(max_examples=50, database=None, deadline=None)
@given(gpio=st.sampled_from(FLASH_GPIOS))
def test_flash_gpio_assignment_is_rejected(gpio):
    spec = SupportedBuild(
        status="ok",
        title="Soil moisture monitor",
        summary="Soil sensor wired to a flash-reserved GPIO.",
        parts=[
            PartRef(instance_id="board", part_id="esp32_devkit_30pin"),
            PartRef(instance_id="soil", part_id="soil_moisture_capacitive"),
        ],
        connections=[
            Connection(
                from_instance="soil",
                from_pin="VCC",
                to_instance="board",
                to_pin="3V3",
                signal_role="rail",
                rail_name="3V3",
            ),
            Connection(
                from_instance="soil",
                from_pin="AOUT",
                to_instance="board",
                to_pin=gpio,
                signal_role="signal",
            ),
            Connection(
                from_instance="soil",
                from_pin="GND",
                to_instance="board",
                to_pin="GND",
                signal_role="gnd",
            ),
        ],
        layout=[LayoutHint(part_id="soil_moisture_capacitive", mount="wall")],
        behaviors=[],
    )
    report = run_all(spec, CATALOG)
    assert "GPIO_FLASH_RESERVED" in _error_codes(report)


@settings(max_examples=50, database=None, deadline=None)
@given(gpio=st.sampled_from(OUTPUT_GPIOS))
def test_non_flash_gpio_does_not_trigger_flash_rule(gpio):
    spec = SupportedBuild(
        status="ok",
        title="Soil moisture monitor",
        summary="Soil sensor wired to a general-purpose GPIO.",
        parts=[
            PartRef(instance_id="board", part_id="esp32_devkit_30pin"),
            PartRef(instance_id="soil", part_id="soil_moisture_capacitive"),
        ],
        connections=[
            Connection(
                from_instance="soil",
                from_pin="VCC",
                to_instance="board",
                to_pin="3V3",
                signal_role="rail",
                rail_name="3V3",
            ),
            Connection(
                from_instance="soil",
                from_pin="AOUT",
                to_instance="board",
                to_pin=gpio,
                signal_role="signal",
            ),
            Connection(
                from_instance="soil",
                from_pin="GND",
                to_instance="board",
                to_pin="GND",
                signal_role="gnd",
            ),
        ],
        layout=[LayoutHint(part_id="soil_moisture_capacitive", mount="wall")],
        behaviors=[],
    )
    report = run_all(spec, CATALOG)
    assert "GPIO_FLASH_RESERVED" not in _error_codes(report)


@settings(max_examples=30, database=None, deadline=None)
@given(ids=st.lists(INSTANCE_IDS, min_size=2, max_size=2, unique=True))
def test_duplicate_oled_addresses_are_rejected(ids):
    screen_a, screen_b = ids
    spec = SupportedBuild(
        status="ok",
        title="Two OLEDs",
        summary="Two identical OLED modules on one I2C bus.",
        parts=[
            PartRef(instance_id="board", part_id="esp32_devkit_30pin"),
            PartRef(instance_id=screen_a, part_id="ssd1306_oled_096"),
            PartRef(instance_id=screen_b, part_id="ssd1306_oled_096"),
        ],
        connections=[
            Connection(
                from_instance=screen_a,
                from_pin="VCC",
                to_instance="board",
                to_pin="3V3",
                signal_role="rail",
                rail_name="3V3",
            ),
            Connection(
                from_instance=screen_a,
                from_pin="GND",
                to_instance="board",
                to_pin="GND",
                signal_role="gnd",
            ),
            Connection(
                from_instance=screen_a,
                from_pin="SDA",
                to_instance="board",
                to_pin="D21",
                signal_role="sda",
            ),
            Connection(
                from_instance=screen_a,
                from_pin="SCL",
                to_instance="board",
                to_pin="D22",
                signal_role="scl",
            ),
            Connection(
                from_instance=screen_b,
                from_pin="VCC",
                to_instance="board",
                to_pin="3V3",
                signal_role="rail",
                rail_name="3V3",
            ),
            Connection(
                from_instance=screen_b,
                from_pin="GND",
                to_instance="board",
                to_pin="GND",
                signal_role="gnd",
            ),
            Connection(
                from_instance=screen_b,
                from_pin="SDA",
                to_instance="board",
                to_pin="D21",
                signal_role="sda",
            ),
            Connection(
                from_instance=screen_b,
                from_pin="SCL",
                to_instance="board",
                to_pin="D22",
                signal_role="scl",
            ),
        ],
        layout=[
            LayoutHint(part_id="ssd1306_oled_096", mount="lid"),
            LayoutHint(part_id="ssd1306_oled_096", mount="lid"),
        ],
        behaviors=[],
    )
    report = run_all(spec, CATALOG)
    assert "I2C_ADDRESS_CONFLICT" in _error_codes(report)


@settings(max_examples=30, database=None, deadline=None)
@given(instance_id=INSTANCE_IDS)
def test_single_oled_does_not_report_address_conflict(instance_id):
    spec = SupportedBuild(
        status="ok",
        title="One OLED",
        summary="A single OLED on the I2C bus.",
        parts=[
            PartRef(instance_id="board", part_id="esp32_devkit_30pin"),
            PartRef(instance_id=instance_id, part_id="ssd1306_oled_096"),
        ],
        connections=[
            Connection(
                from_instance=instance_id,
                from_pin="VCC",
                to_instance="board",
                to_pin="3V3",
                signal_role="rail",
                rail_name="3V3",
            ),
            Connection(
                from_instance=instance_id,
                from_pin="GND",
                to_instance="board",
                to_pin="GND",
                signal_role="gnd",
            ),
            Connection(
                from_instance=instance_id,
                from_pin="SDA",
                to_instance="board",
                to_pin="D21",
                signal_role="sda",
            ),
            Connection(
                from_instance=instance_id,
                from_pin="SCL",
                to_instance="board",
                to_pin="D22",
                signal_role="scl",
            ),
        ],
        layout=[LayoutHint(part_id="ssd1306_oled_096", mount="lid")],
        behaviors=[],
    )
    report = run_all(spec, CATALOG)
    assert "I2C_ADDRESS_CONFLICT" not in _error_codes(report)


@settings(max_examples=30, database=None, deadline=None)
@given(instance_id=INSTANCE_IDS)
def test_servo_on_five_volt_rail_is_over_budget(instance_id):
    spec = SupportedBuild(
        status="ok",
        title="Servo on USB power",
        summary="A servo powered from the 5 V rail.",
        parts=[
            PartRef(instance_id="board", part_id="esp32_devkit_30pin"),
            PartRef(instance_id=instance_id, part_id="sg90_servo"),
        ],
        connections=[
            Connection(
                from_instance=instance_id,
                from_pin="VCC",
                to_instance="board",
                to_pin="5V",
                signal_role="rail",
                rail_name="5V",
            ),
            Connection(
                from_instance=instance_id,
                from_pin="GND",
                to_instance="board",
                to_pin="GND",
                signal_role="gnd",
            ),
            Connection(
                from_instance=instance_id,
                from_pin="SIG",
                to_instance="board",
                to_pin="D4",
                signal_role="signal",
            ),
        ],
        layout=[LayoutHint(part_id="sg90_servo", mount="base")],
        behaviors=[],
    )
    report = run_all(spec, CATALOG)
    assert "RAIL_OVER_BUDGET" in _error_codes(report)


@settings(max_examples=50, database=None, deadline=None)
@given(
    ids=st.lists(INSTANCE_IDS, min_size=4, max_size=4, unique=True),
    soil_gpio=st.sampled_from(ADC_GPIOS),
    env_gpio=st.sampled_from(OUTPUT_GPIOS),
    mount=st.sampled_from(["base", "lid", "wall"]),
)
def test_renamed_valid_soil_monitor_remains_valid(ids, soil_gpio, env_gpio, mount):
    from hypothesis import assume

    assume(soil_gpio != env_gpio)
    board_id, soil_id, env_id, screen_id = ids
    spec = _soil_spec(board_id, soil_id, env_id, screen_id, soil_gpio, env_gpio, mount)
    report = run_all(spec, CATALOG)
    assert report.passed
    assert report.issues == []


@settings(max_examples=30, database=None, deadline=None)
@given(instance_id=INSTANCE_IDS)
def test_soil_monitor_without_servo_is_not_over_budget(instance_id):
    spec = _soil_spec(
        "board",
        "soil",
        "env",
        "screen",
        "D32",
        "D4",
        "wall",
    )
    report = run_all(spec, CATALOG)
    assert "RAIL_OVER_BUDGET" not in _error_codes(report)
