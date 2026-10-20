"""Shared helpers for Phase 3 planner tests. Offline: FakeLLM only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sparkplan.models.buildspec import SupportedBuild, parse_build_spec
from sparkplan.models.catalog import Catalog, get_catalog

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "phase3"


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return get_catalog()


def load_spec(name: str) -> dict:
    """Raw BuildSpec JSON (as the LLM would emit it) from a fixture."""
    return json.loads((FIXTURES / name).read_text())


def valid_spec() -> dict:
    return load_spec("supported/soil_monitor.json")


def unsupported_spec() -> dict:
    return load_spec("unsupported/drone.json")


def as_supported(data: dict) -> SupportedBuild:
    spec = parse_build_spec(data)
    assert isinstance(spec, SupportedBuild)
    return spec
