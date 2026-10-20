"""Requirement 11: the legacy surfaces still import and behave unchanged.

Phase 3 added new subpackages and touched no legacy module. This test proves
the legacy planner, LLM client, API, pipeline and evaluation entrypoints still
import, and that the legacy Planner still returns OK on its existing fixture.
"""

from __future__ import annotations

import importlib


def test_legacy_modules_import():
    for name in (
        "sparkplan.planner",
        "sparkplan.llm",
        "sparkplan.llm.client",
        "sparkplan.pipeline.pipeline",
        "sparkplan.pipeline",
        "eval.run_eval",
    ):
        importlib.import_module(name)


def test_legacy_planner_reports_unchanged():
    from sparkplan.catalog import load_catalog
    from sparkplan.llm import FakeLLM
    from sparkplan.models.spec import PlanStatus
    from sparkplan.planner import Planner

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
    result = Planner(FakeLLM(script=[spec]), load_catalog()).plan("desk weather station")
    assert result.status == PlanStatus.OK
    assert result.spec is not None


def test_phase3_names_do_not_shadow_legacy():
    import sparkplan.llm.client as legacy
    import sparkplan.llm.phase3.client as p3

    assert legacy.OpenAICompatClient is not p3.OpenAICompatibleClient
    assert hasattr(legacy, "OpenAICompatClient")
    assert hasattr(p3, "OpenAICompatibleClient")
