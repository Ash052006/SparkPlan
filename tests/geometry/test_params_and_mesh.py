from __future__ import annotations

import numpy as np
import pytest
from sparkplan.catalog import load_catalog
from sparkplan.geometry import (
    GeometryEngineError,
    available_engines,
    build_enclosure,
    compute_enclosure_plan,
)
from tests.buildspecs import make_spec


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.fixture(scope="module")
def plan(catalog):
    return compute_enclosure_plan(make_spec(), catalog)


def test_plan_fits_all_non_power_parts(plan, catalog):
    spec = make_spec()
    placed = {p.instance_id for p in plan.params.placements}
    assert "controller" in placed
    for inst in spec.parts:
        assert inst.instance_id in placed


def test_controller_footprint_matches_catalog(plan, catalog):
    ctrl = next(p for p in plan.params.placements if p.instance_id == "controller")
    m = catalog.controller.mechanical
    assert ctrl.footprint_x_mm == m.length_mm
    assert ctrl.footprint_y_mm == m.width_mm


def test_usb_cutout_present_and_sized(plan, catalog):
    usb_cuts = [c for c in plan.params.cutouts if c.kind == "usb"]
    assert len(usb_cuts) == 1
    usb = catalog.controller.mechanical.usb_connector
    assert usb_cuts[0].width_mm >= usb.width_mm
    assert usb_cuts[0].wall == "-x"


def test_cable_notches_for_peripherals(plan):
    notches = [c for c in plan.params.cutouts if c.kind == "cable_notch"]
    peripherals = [p for p in plan.params.placements if p.instance_id != "controller"]
    assert len(notches) == len(peripherals)


def test_no_placements_overlap(plan):
    placements = plan.params.placements
    for i, a in enumerate(placements):
        for b in placements[i + 1 :]:
            ax2, ay2 = a.x_mm + a.footprint_x_mm, a.y_mm + a.footprint_y_mm
            bx2, by2 = b.x_mm + b.footprint_x_mm, b.y_mm + b.footprint_y_mm
            overlaps = a.x_mm < bx2 and b.x_mm < ax2 and a.y_mm < by2 and b.y_mm < ay2
            assert not overlaps, f"{a.instance_id} overlaps {b.instance_id}"


def test_inner_dims_clear_the_parts(plan):
    p = plan.params
    max_x = max(pl.x_mm + pl.footprint_x_mm for pl in p.placements)
    max_y = max(pl.y_mm + pl.footprint_y_mm for pl in p.placements)
    assert max_x <= p.inner_x_mm
    assert max_y <= p.inner_y_mm


@pytest.mark.skipif("cadquery" not in available_engines(), reason="cadquery not installed")
def test_cadquery_meshes_are_solid_and_sized(plan):
    meshes = build_enclosure(plan)
    p = plan.params
    base = meshes.base_trimesh()
    lid = meshes.lid_trimesh()
    assert base.is_watertight and base.volume > 0
    assert lid.is_watertight and lid.volume > 0
    b_ext = base.bounding_box.extents
    assert b_ext[0] == pytest.approx(p.outer_x_mm, abs=0.3)
    assert b_ext[1] == pytest.approx(p.outer_y_mm, abs=0.3)
    assert b_ext[2] == pytest.approx(p.base_height_mm, abs=0.3)
    l_ext = lid.bounding_box.extents
    assert l_ext[0] == pytest.approx(p.outer_x_mm, abs=0.3)
    assert l_ext[1] == pytest.approx(p.outer_y_mm, abs=0.3)
    assert l_ext[2] == pytest.approx(p.lid_mm + p.lid_lip_mm, abs=0.3)


@pytest.mark.skipif("cadquery" not in available_engines(), reason="cadquery not installed")
def test_base_has_usb_hole(plan, tmp_path):
    meshes = build_enclosure(plan)
    path = meshes.export_base_stl(tmp_path / "base.stl")
    assert path.exists() and path.stat().st_size > 1000
    import trimesh

    loaded = trimesh.load(path)
    assert loaded.is_watertight
    # A hole in the -x wall means no material at the wall centre point.
    p = plan.params
    usb = next(c for c in p.cutouts if c.kind == "usb")
    query = np.array(
        [
            [
                -p.outer_x_mm / 2.0 + 0.5,
                usb.center_mm - p.inner_y_mm / 2.0,
                usb.bottom_mm + usb.height_mm / 2.0,
            ]
        ]
    )
    assert not loaded.contains(query)[0]


@pytest.mark.skipif("build123d" not in available_engines(), reason="build123d not installed")
def test_build123d_fallback_solid(plan):
    from sparkplan.geometry import build123d_engine

    meshes = build123d_engine.build(plan)
    assert meshes.base_trimesh().is_watertight
    assert meshes.base_trimesh().volume > 0


def test_no_engine_raises(monkeypatch):
    import sparkplan.geometry.engine as engine_mod

    monkeypatch.setattr(engine_mod, "available_engines", lambda: [])
    with pytest.raises(GeometryEngineError):
        engine_mod.build_enclosure(None)
