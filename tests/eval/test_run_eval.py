from __future__ import annotations

from eval.run_eval import load_cases, run_eval
from sparkplan.catalog import load_catalog
from sparkplan.llm import FakeLLM
from sparkplan.models.spec import PlanStatus

CATALOG = load_catalog()


def test_refusal_cases_pass_without_llm():
    cases = [c for c in load_cases() if c["expect"] == "refused"]
    llm = FakeLLM(script=[])
    report = run_eval(cases, llm, CATALOG)
    assert report["passed"] == report["total"]
    assert llm.calls == [], "deterministic refusals must not consult the LLM"


def test_supported_cases_with_scripted_specs():
    cases = [
        {
            "idea": "desk weather station",
            "expect": "supported",
            "expected_parts_any_of": [["dht22"]],
        }
    ]
    spec = {
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
    report = run_eval(cases, FakeLLM(script=[spec]), CATALOG)
    assert report["passed"] == 1


def test_scoring_rejects_wrong_part():
    cases = [{"idea": "x", "expect": "supported", "expected_parts_any_of": [["bmp280"]]}]
    spec = {
        "idea": "x",
        "controller": "esp32_devkit_v4",
        "parts": [
            {"instance_id": "baro", "part_id": "bmp280"},
        ],
        "connections": [
            {"instance_id": "baro", "peripheral_pin": "SDA", "controller_pin": "GPIO21"},
            {"instance_id": "baro", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
        ],
        "actions": [
            {
                "action_id": "read_p",
                "kind": "read",
                "instance_id": "baro",
                "api": "read_pressure_hpa",
            },
        ],
    }
    report = run_eval(cases, FakeLLM(script=[spec]), CATALOG)
    assert report["passed"] == 1
    assert report["cases"][0]["status"] == PlanStatus.OK.value
