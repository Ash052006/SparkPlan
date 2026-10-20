"""Phase 1 BuildSpec tests: supported/unsupported union + JSON round-trip."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sparkplan.models.buildspec import (
    SupportedBuild,
    UnsupportedBuild,
    build_spec_json_schema,
    parse_build_spec,
)

SUPPORTED = {
    "status": "ok",
    "title": "Temperature logger",
    "summary": "Reads a DHT22 and shows the value on an OLED.",
    "parts": [
        {"instance_id": "board", "part_id": "esp32_devkit_30pin"},
        {"instance_id": "temp", "part_id": "dht22"},
        {"instance_id": "screen", "part_id": "ssd1306_oled_096"},
    ],
    "connections": [
        {
            "from_instance": "temp",
            "from_pin": "DATA",
            "to_instance": "board",
            "to_pin": "D4",
        }
    ],
    "layout": [{"part_id": "ssd1306_oled_096", "mount": "lid"}],
    "behaviors": [{"name": "display_temperature", "part_ids": ["dht22", "ssd1306_oled_096"]}],
}

UNSUPPORTED = {
    "status": "unsupported",
    "title": "Laser rangefinder",
    "reasons": ["No laser emitter is in the catalog."],
    "missing_capabilities": ["laser_ranging"],
    "closest_supported_alternative": "Use the HC-SR04 ultrasonic sensor instead.",
}


def test_supported_build_parses():
    spec = parse_build_spec(SUPPORTED)
    assert isinstance(spec, SupportedBuild)
    assert spec.status == "ok"
    assert spec.part_on("temp") == "dht22"
    assert spec.instance("board").part_id == "esp32_devkit_30pin"


def test_unsupported_build_parses():
    spec = parse_build_spec(UNSUPPORTED)
    assert isinstance(spec, UnsupportedBuild)
    assert spec.status == "unsupported"
    assert spec.missing_capabilities == ["laser_ranging"]


def test_supported_round_trips_through_json():
    spec = parse_build_spec(SUPPORTED)
    dumped = spec.model_dump_json()
    again = parse_build_spec(spec.model_dump())
    assert again == spec
    rehydrated = SupportedBuild.model_validate_json(dumped)
    assert rehydrated == spec


def test_unsupported_round_trips_through_json():
    spec = parse_build_spec(UNSUPPORTED)
    rehydrated = UnsupportedBuild.model_validate_json(spec.model_dump_json())
    assert rehydrated == spec


def test_duplicate_instance_ids_rejected():
    bad = dict(SUPPORTED)
    bad["parts"] = [
        {"instance_id": "board", "part_id": "esp32_devkit_30pin"},
        {"instance_id": "board", "part_id": "dht22"},
    ]
    with pytest.raises(ValidationError):
        parse_build_spec(bad)


def test_supported_build_requires_board():
    bad = dict(SUPPORTED)
    bad["parts"] = [{"instance_id": "temp", "part_id": "dht22"}]
    with pytest.raises(ValidationError):
        parse_build_spec(bad)


def test_unsupported_requires_at_least_one_reason():
    bad = dict(UNSUPPORTED)
    bad["reasons"] = []
    with pytest.raises(ValidationError):
        parse_build_spec(bad)


def test_unknown_field_rejected():
    bad = dict(SUPPORTED)
    bad["surprise"] = True
    with pytest.raises(ValidationError):
        parse_build_spec(bad)


def test_unknown_status_rejected():
    with pytest.raises(ValidationError):
        parse_build_spec({"status": "maybe", "title": "x"})


def test_json_schema_is_discriminated():
    schema = build_spec_json_schema()
    assert "oneOf" in schema or "anyOf" in schema
