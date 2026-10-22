"""Layout alias regression tests for real-model output shapes."""

from __future__ import annotations

from sparkplan.llm.phase3 import FakeLLM
from sparkplan.planner.phase3 import Planner, PlanStatus
from sparkplan.planner.phase3.normalize import (
    canonicalize_connection_directions,
    canonicalize_layout_aliases,
)
from tests.planner.phase3.conftest import valid_spec


def _layout_by_instance() -> dict:
    spec = valid_spec()
    aliases = {
        "soil_moisture_capacitive": "soil",
        "dht22": "env",
        "ssd1306_oled_096": "screen",
    }
    for hint in spec["layout"]:
        hint["part_id"] = aliases[hint["part_id"]]
    return spec


def test_layout_instance_ids_are_canonicalized(catalog):
    normalized = canonicalize_layout_aliases(_layout_by_instance(), catalog)
    assert isinstance(normalized, dict)
    assert [hint["part_id"] for hint in normalized["layout"]] == [
        "soil_moisture_capacitive",
        "dht22",
        "ssd1306_oled_096",
    ]


def test_layout_aliases_are_deduplicated(catalog):
    spec = _layout_by_instance()
    spec["layout"].append({"part_id": "screen", "mount": "base"})
    normalized = canonicalize_layout_aliases(spec, catalog)
    assert isinstance(normalized, dict)
    assert [hint["part_id"] for hint in normalized["layout"]].count("ssd1306_oled_096") == 1


def test_unknown_layout_id_is_left_for_the_validator(catalog):
    spec = valid_spec()
    spec["layout"][0]["part_id"] = "not_a_part_or_instance"
    normalized = canonicalize_layout_aliases(spec, catalog)
    assert isinstance(normalized, dict)
    assert normalized["layout"][0]["part_id"] == "not_a_part_or_instance"


def test_planner_accepts_instance_ids_in_layout(catalog):
    llm = FakeLLM(script=[_layout_by_instance()])
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.status is PlanStatus.OK
    assert result.llm_calls == 1
    assert result.spec is not None
    assert [hint.part_id for hint in result.spec.layout] == [
        "soil_moisture_capacitive",
        "dht22",
        "ssd1306_oled_096",
    ]


def _board_first() -> dict:
    spec = valid_spec()
    for connection in spec["connections"]:
        if connection["to_instance"] == "board":
            connection["from_instance"], connection["to_instance"] = (
                connection["to_instance"],
                connection["from_instance"],
            )
            connection["from_pin"], connection["to_pin"] = (
                connection["to_pin"],
                connection["from_pin"],
            )
    return spec


def test_board_first_connections_are_reversed(catalog):
    normalized = canonicalize_connection_directions(_board_first(), catalog)
    assert isinstance(normalized, dict)
    first = normalized["connections"][0]
    assert first["from_instance"] == "soil"
    assert first["from_pin"] == "VCC"
    assert first["to_instance"] == "board"
    assert first["to_pin"] == "3V3"


def test_planner_accepts_board_first_connections(catalog):
    llm = FakeLLM(script=[_board_first()])
    result = Planner(llm, catalog).plan("a desk plant monitor")
    assert result.status is PlanStatus.OK
    assert result.llm_calls == 1
    assert result.spec is not None
    assert all(connection.to_instance == "board" for connection in result.spec.connections)


def test_peripheral_to_peripheral_connections_are_unchanged(catalog):
    spec = valid_spec()
    spec["connections"] = [
        {
            "from_instance": "env",
            "from_pin": "DATA",
            "to_instance": "soil",
            "to_pin": "AOUT",
            "signal_role": "signal",
        }
    ]
    normalized = canonicalize_connection_directions(spec, catalog)
    assert isinstance(normalized, dict)
    assert normalized["connections"] == spec["connections"]


def test_unsupported_payload_is_untouched(catalog):
    payload = {"status": "unsupported", "title": "x", "reasons": ["y"], "layout": []}
    assert canonicalize_layout_aliases(payload, catalog) == payload
