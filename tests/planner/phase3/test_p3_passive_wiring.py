"""Planner wiring rules for two-terminal passives. Offline: FakeLLM only."""

from __future__ import annotations

import copy

from sparkplan.llm.phase3 import FakeLLM
from sparkplan.models.buildspec import SupportedBuild, parse_build_spec
from sparkplan.planner.phase3 import Planner, PlanStatus
from sparkplan.planner.phase3.prompt import build_system_prompt
from sparkplan.validators.phase2.pins_check import validate_pins
from sparkplan.validators.phase2.report import run_all
from tests.planner.phase3.conftest import load_spec


def _series_resistor_spec() -> dict:
    return load_spec("supported/buzzer_series_resistor.json")


def _invented_resistor_gnd_spec() -> dict:
    spec = copy.deepcopy(_series_resistor_spec())
    for connection in spec["connections"]:
        if connection["from_instance"] == "res" and connection["from_pin"] == "B":
            connection["from_pin"] = "GND"
    return spec


def test_series_resistor_fixture_uses_declared_pins_and_validates(catalog):
    spec = parse_build_spec(_series_resistor_spec())
    assert isinstance(spec, SupportedBuild)

    pin_issues = validate_pins(spec, catalog, catalog.board().board)
    assert not [issue for issue in pin_issues if issue.code == "PIN_UNKNOWN"]
    assert run_all(spec, catalog).passed

    series = [
        connection
        for connection in spec.connections
        if connection.from_instance == "buzzer" and connection.to_instance == "res"
    ]
    assert len(series) == 1
    assert series[0].from_pin == "NEG"
    assert series[0].to_pin == "A"


def test_resistor_invented_gnd_pin_is_rejected(catalog):
    spec = parse_build_spec(_invented_resistor_gnd_spec())
    assert isinstance(spec, SupportedBuild)

    pin_issues = validate_pins(spec, catalog, catalog.board().board)
    unknown = [issue for issue in pin_issues if issue.code == "PIN_UNKNOWN"]
    assert len(unknown) == 1
    assert unknown[0].part_ids == ["res"]
    assert "A" in unknown[0].fix_hint and "B" in unknown[0].fix_hint
    assert not run_all(spec, catalog).passed


def test_initial_prompt_has_no_blanket_vcc_gnd_rule(catalog):
    system = build_system_prompt(catalog)
    lowered = system.lower()

    assert "for every non-board part:" not in lowered
    assert "only for parts whose declared pins include vcc/gnd" in lowered
    assert "part-to-part" in lowered
    assert "resistor_220r has only a and b" in lowered


def test_unknown_resistor_pin_repair_is_specific_and_succeeds(catalog):
    invalid = _invented_resistor_gnd_spec()
    valid = _series_resistor_spec()
    llm = FakeLLM(script=[invalid, valid])

    result = Planner(llm, catalog).plan("a buzzer with a series resistor")

    assert result.status is PlanStatus.OK
    assert result.repair_rounds == 1
    assert result.spec is not None
    assert not any(
        connection.from_instance == "res" and connection.from_pin == "GND"
        for connection in result.spec.connections
    )

    repair = llm.calls[1]["user"]
    assert "res (resistor_220r) is a two-terminal passive" in repair
    assert "only pins A and B" in repair
    assert "circuit nodes" in repair
    assert "GPIO2" not in repair
    assert "for EVERY non-board part:" not in llm.calls[1]["system"]
    assert "for EVERY non-board part:" not in repair
