"""Regression tests for behaviour-controlled LED wiring.

The LED template needs its ANODE on a board GPIO. A series resistor on the
cathode return path is valid and does not need firmware bindings.
"""

from __future__ import annotations

from sparkplan.firmware import generate_behavior_firmware
from sparkplan.llm.phase3 import FakeLLM
from sparkplan.models.buildspec import SupportedBuild, parse_build_spec
from sparkplan.planner.phase3 import Planner, PlanStatus
from sparkplan.planner.phase3.prompt import build_system_prompt
from sparkplan.validators.phase2.report import run_all
from tests.planner.phase3.conftest import load_spec


def _spec(name: str) -> SupportedBuild:
    spec = parse_build_spec(load_spec(name))
    assert isinstance(spec, SupportedBuild)
    return spec


def test_prompt_requires_led_anode_on_board_gpio_and_cathode_return(catalog):
    text = " ".join(build_system_prompt(catalog).split()).lower()
    assert "actuators: board gpio" in text
    assert "led_5mm anode->gpio" in text
    assert "cathode->220r->gnd" in text
    assert "use action_reading" in text
    assert "action_actuator is unsupported" in text


def test_valid_led_circuit_passes_validation(catalog):
    report = run_all(_spec("supported/motion_led.json"), catalog)
    assert report.passed, [issue.code for issue in report.errors]


def test_supported_motion_led_generates_firmware(catalog):
    spec = _spec("supported/motion_led.json")
    rendered = generate_behavior_firmware(spec, catalog)
    assert rendered.supported
    assert not rendered.errors
    assert "pinMode(led_LED_PIN, OUTPUT);" in rendered.sketch
    expected = (
        'Serial.printf("rule light_on fired: motion=%s\\n", (pir_motion ? "true" : "false"));'
    )
    assert expected in rendered.sketch


def test_rail_powered_led_is_rejected_before_firmware(catalog):
    report = run_all(_spec("invalid/motion_led_rail_powered.json"), catalog)
    control = [issue for issue in report.errors if issue.code == "ACTUATOR_NO_CONTROL_GPIO"]
    assert control, [issue.code for issue in report.errors]
    assert control[0].part_ids == ["led"]
    assert "ANODE -> a board GPIO" in control[0].fix_hint


def test_repair_prompt_guides_led_control_and_valid_spec_passes(catalog):
    bad = load_spec("invalid/motion_led_rail_powered.json")
    good = load_spec("supported/motion_led.json")
    llm = FakeLLM(script=[bad, good])

    result = Planner(llm, catalog).plan("a motion display that lights an LED")

    assert result.status is PlanStatus.OK
    assert result.repair_rounds == 1
    repair = " ".join(llm.calls[1]["user"].split()).lower()
    assert "actuator_no_control_gpio" in repair
    assert "anode -> a board gpio" in repair
    assert "never power the led from a rail" in repair


def test_repair_prompt_guides_unsupported_actuator_action_to_action_reading(catalog):
    bad = load_spec("supported/motion_led.json")
    bad["behaviors"][0]["rules"][0]["action_actuator"] = "led"
    bad["behaviors"][0]["rules"][0]["action_reading"] = ""
    good = load_spec("supported/motion_led.json")
    llm = FakeLLM(script=[bad, good])

    result = Planner(llm, catalog).plan("a motion display that lights an LED")

    assert result.status is PlanStatus.OK
    assert result.repair_rounds == 1
    repair = " ".join(llm.calls[1]["user"].split()).lower()
    assert "behavior_rule_actuator_unsupported" in repair
    assert "action_actuator must be empty" in repair
