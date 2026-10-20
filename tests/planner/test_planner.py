from __future__ import annotations

import json

import pytest
from sparkplan.catalog import load_catalog
from sparkplan.llm import FakeLLM
from sparkplan.models.spec import PlanStatus
from sparkplan.planner import MAX_REPAIR_ROUNDS, Planner

VALID_SPEC = {
    "idea": "desk weather station",
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
            "action_id": "show",
            "kind": "write",
            "instance_id": "screen",
            "api": "print_line1",
            "consumes": "read_temp",
        },
    ],
}


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def test_happy_path_single_shot(catalog):
    llm = FakeLLM(script=[VALID_SPEC])
    result = Planner(llm, catalog).plan("desk weather station")
    assert result.status == PlanStatus.OK
    assert result.spec is not None
    assert result.repair_rounds == 0
    assert result.spec.instance("env") is not None


def test_repair_round_fixes_unknown_part(catalog):
    bad = dict(VALID_SPEC)
    bad["parts"] = [
        {"instance_id": "env", "part_id": "dht11"},
        {"instance_id": "screen", "part_id": "ssd1306_oled"},
    ]
    llm = FakeLLM(script=[bad, VALID_SPEC])
    result = Planner(llm, catalog).plan("desk weather station")
    assert result.status == PlanStatus.OK
    assert result.repair_rounds == 1
    repair_call = llm.calls[1]["user"]
    assert "UNKNOWN_PART" in repair_call
    assert "dht11" in repair_call


def test_repair_budget_exhausted(catalog):
    bad = dict(VALID_SPEC)
    bad["parts"] = [{"instance_id": "env", "part_id": "camera_ov2640"}]
    bad["connections"] = []
    bad["actions"] = []
    llm = FakeLLM(script=[bad] * 10)
    result = Planner(llm, catalog).plan("weather station")
    assert result.status == PlanStatus.EXHAUSTED
    assert result.refusal is not None
    assert any("UNKNOWN_PART" in r for r in result.refusal.reasons)
    assert result.repair_rounds == MAX_REPAIR_ROUNDS
    assert len(llm.calls) == MAX_REPAIR_ROUNDS + 1


def test_llm_refusal_is_respected(catalog):
    llm = FakeLLM(
        script=[
            {
                "supported": False,
                "reasons": ["a joke-telling toy needs speech synthesis, outside the catalog"],
                "detected_capabilities": [],
            }
        ]
    )
    result = Planner(llm, catalog).plan("a desk toy that tells jokes out loud")
    assert result.status == PlanStatus.REFUSED
    assert result.refusal is not None
    assert any("speech synthesis" in r for r in result.refusal.reasons)


def test_deterministic_refusal_without_llm_call(catalog):
    llm = FakeLLM(script=[])
    result = Planner(llm, catalog).plan("GPS tracker with camera and SD card")
    assert result.status == PlanStatus.REFUSED
    assert llm.calls == []
    assert result.refusal is not None
    joined = " ".join(result.refusal.reasons).lower()
    assert "camera" in joined
    assert "gps" in joined


def test_parse_error_goes_through_repair(catalog):
    llm = FakeLLM(script=[{"parts": "not-a-list"}, VALID_SPEC])
    result = Planner(llm, catalog).plan("desk weather station")
    assert result.status == PlanStatus.OK
    assert result.repair_rounds == 1
    assert "SPEC_PARSE_ERROR" in llm.calls[1]["user"]


def test_empty_idea_refused(catalog):
    result = Planner(FakeLLM(script=[]), catalog).plan("   ")
    assert result.status == PlanStatus.REFUSED


def test_validator_errors_do_not_leak_through(catalog):
    # A spec with a real validator error (GPIO conflict) must not pass.
    conflicting = json.loads(json.dumps(VALID_SPEC))
    conflicting["connections"].append(
        {"instance_id": "screen", "peripheral_pin": "SCL", "controller_pin": "GPIO21"}
    )
    llm = FakeLLM(script=[conflicting] * (MAX_REPAIR_ROUNDS + 1))
    result = Planner(llm, catalog).plan("weather station")
    assert result.status == PlanStatus.EXHAUSTED
    assert any(i.code == "GPIO_CONFLICT" for i in result.issues)
