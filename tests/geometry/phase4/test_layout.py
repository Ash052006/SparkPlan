"""Phase 4 deterministic layout tests. Offline: no network, no LLM."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sparkplan.geometry.enclosure import EnclosureConfig
from sparkplan.geometry.layout import pack_enclosure
from sparkplan.models.buildspec import parse_build_spec
from sparkplan.models.catalog import Catalog, get_catalog

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _spec(name: str):
    data = json.loads((FIXTURES / name).read_text())
    return parse_build_spec(data)


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return get_catalog()


def test_soil_monitor_packs(catalog: Catalog) -> None:
    layout, issues = pack_enclosure(_spec("phase3/supported/soil_monitor.json"), catalog)
    assert layout is not None
    assert not [i for i in issues if i.severity == "error"]
    assert layout.inner_x_mm <= layout.config.inner_max_x_mm
    assert layout.inner_y_mm <= layout.config.inner_max_y_mm
    assert layout.inner_z_mm <= layout.config.inner_max_z_mm
    assert len(layout.parts) == 4
    assert len(layout.posts) == 4


def test_soil_probe_is_pass_through(catalog: Catalog) -> None:
    layout, issues = pack_enclosure(_spec("phase3/supported/soil_monitor.json"), catalog)
    assert layout is not None
    soil = next(p for p in layout.parts if p.instance_id == "soil")
    # 98 mm prong: head clamped to the cavity, rest protrudes through the floor
    assert soil.size_z_mm == pytest.approx(layout.inner_z_mm)
    assert soil.protrudes_mm > 0.0
    assert soil.protrudes_mm + soil.size_z_mm == pytest.approx(98.0)
    slot = next(o for o in layout.openings if o.face == "floor")
    assert soil.instance_id in slot.parts
    assert not [i for i in issues if i.severity == "error"]


def test_board_usb_opening_on_wall(catalog: Catalog) -> None:
    layout, _ = pack_enclosure(_spec("phase3/supported/soil_monitor.json"), catalog)
    assert layout is not None
    board = next(p for p in layout.parts if p.instance_id == "board")
    assert board.wall == "-x"
    opening = next(o for o in layout.openings if "board" in o.parts)
    assert opening.face == "-x"
    # height above the cavity floor, inside the wall face bounds
    assert opening.cy_mm - opening.height_mm / 2.0 >= 0.0
    assert opening.cy_mm + opening.height_mm / 2.0 <= layout.inner_z_mm


def test_valid_divider_six_parts_pack(catalog: Catalog) -> None:
    layout, issues = pack_enclosure(_spec("phase2/v5/valid_divider.json"), catalog)
    assert layout is not None, [i.message for i in issues]
    assert not [i for i in issues if i.severity == "error"]
    assert len(layout.parts) == 6
    assert {p.instance_id for p in layout.parts} == {
        "board",
        "soil",
        "env",
        "screen",
        "range",
        "div",
    }


def test_ultrasonic_alarm_packs(catalog: Catalog) -> None:
    layout, issues = pack_enclosure(_spec("phase4/ultrasonic_alarm.json"), catalog)
    assert layout is not None, [i.message for i in issues]
    assert not [i for i in issues if i.severity == "error"]
    assert len(layout.parts) == 4
    # the HC-SR04 window is a wall cutout for the transducers
    window = next(o for o in layout.openings if "range" in o.parts)
    assert window.face in ("-x", "+x", "-y", "+y")
    assert window.width_mm >= 40.0


def test_layout_is_deterministic(catalog: Catalog) -> None:
    spec = _spec("phase2/v5/valid_divider.json")
    first, _ = pack_enclosure(spec, catalog)
    second, _ = pack_enclosure(spec, catalog)
    assert first is not None and second is not None
    assert first.model_dump() == second.model_dump()


def test_unknown_part_is_an_error(catalog: Catalog) -> None:
    spec = _spec("phase4/ultrasonic_alarm.json")
    spec.parts[1].part_id = "not_in_catalog"
    layout, issues = pack_enclosure(spec, catalog)
    assert layout is None
    assert any(i.code == "GEOMETRY_UNKNOWN_PART" for i in issues)


def test_part_too_large_for_small_build(catalog: Catalog) -> None:
    tiny = EnclosureConfig(build_x_mm=50.0, build_y_mm=50.0, build_z_mm=30.0)
    layout, issues = pack_enclosure(_spec("phase3/supported/soil_monitor.json"), catalog, tiny)
    assert layout is None
    assert any(
        i.code in ("GEOMETRY_PART_TOO_LARGE", "GEOMETRY_LAYOUT_TOO_LARGE") and i.severity == "error"
        for i in issues
    )


def test_too_tall_for_build(catalog: Catalog) -> None:
    squat = EnclosureConfig(build_x_mm=220.0, build_y_mm=220.0, build_z_mm=10.0)
    layout, issues = pack_enclosure(_spec("phase3/supported/soil_monitor.json"), catalog, squat)
    assert layout is None
    assert any(i.code == "GEOMETRY_TOO_TALL" for i in issues)
