"""CadQuery enclosure builder (preferred engine)."""

from __future__ import annotations

import cadquery as cq
import numpy as np

from sparkplan.geometry.engine import EnclosureMeshes, Mesh
from sparkplan.geometry.params import EnclosureParams, EnclosurePlan

_TOL = 0.05


def _to_mesh(solid: cq.Shape) -> Mesh:
    verts, tris = solid.tessellate(_TOL)
    v = [[p.x, p.y, p.z] for p in verts]
    f = [[t[0], t[1], t[2]] for t in tris]
    return Mesh(vertices=np.asarray(v, dtype=np.float64), faces=np.asarray(f, dtype=np.int64))


def _boss_centers(p: EnclosureParams) -> list[tuple[float, float]]:
    """Boss centers in the centered coordinate frame of the outer box."""
    inset = p.wall_mm + p.boss_od_mm / 2.0 + 0.5
    return [
        (inset - p.outer_x_mm / 2.0, inset - p.outer_y_mm / 2.0),
        (p.outer_x_mm / 2.0 - inset, inset - p.outer_y_mm / 2.0),
        (inset - p.outer_x_mm / 2.0, p.outer_y_mm / 2.0 - inset),
        (p.outer_x_mm / 2.0 - inset, p.outer_y_mm / 2.0 - inset),
    ]


def _cutouts_apply(solid: cq.Workplane, p: EnclosureParams) -> cq.Workplane:
    over = 0.4
    for cut in p.cutouts:
        y_centered = cut.center_mm - p.inner_y_mm / 2.0
        z_center = cut.bottom_mm + cut.height_mm / 2.0
        if cut.wall == "-x":
            origin = (-p.outer_x_mm / 2.0, 0, 0)
        elif cut.wall == "+x":
            origin = (p.outer_x_mm / 2.0, 0, 0)
        else:
            continue
        box = (
            cq.Workplane("YZ", origin=origin)
            .center(y_centered, z_center)
            .rect(cut.width_mm, cut.height_mm)
            .extrude(p.wall_mm + over, both=True)
        )
        solid = solid.cut(box)
    return solid


def _build_base(p: EnclosureParams) -> cq.Workplane:
    outer = cq.Workplane("XY").box(
        p.outer_x_mm, p.outer_y_mm, p.base_height_mm, centered=(True, True, False)
    )
    inner = cq.Workplane("XY", origin=(0, 0, p.floor_mm)).box(
        p.inner_x_mm, p.inner_y_mm, p.inner_z_mm + 1.0, centered=(True, True, False)
    )
    body = outer.cut(inner)
    body = _cutouts_apply(body, p)

    for cx, cy in _boss_centers(p):
        boss = (
            cq.Workplane("XY", origin=(cx, cy, p.floor_mm))
            .circle(p.boss_od_mm / 2.0)
            .extrude(p.inner_z_mm)
        )
        hole = (
            cq.Workplane("XY", origin=(cx, cy, p.floor_mm + p.inner_z_mm - p.boss_hole_mm))
            .circle(p.boss_hole_mm / 2.0)
            .extrude(p.boss_hole_mm + 0.2)
        )
        body = body.union(boss).cut(hole)
    return body


def _build_lid(p: EnclosureParams) -> cq.Workplane:
    plate = cq.Workplane("XY").box(
        p.outer_x_mm, p.outer_y_mm, p.lid_mm, centered=(True, True, False)
    )
    lip = cq.Workplane("XY", origin=(0, 0, -p.lid_lip_mm)).box(
        p.inner_x_mm - 0.4, p.inner_y_mm - 0.4, p.lid_lip_mm + 0.1, centered=(True, True, False)
    )
    lid = plate.union(lip)
    for cx, cy in _boss_centers(p):
        hole = (
            cq.Workplane("XY", origin=(cx, cy, -p.lid_lip_mm - 0.2))
            .circle(p.boss_hole_mm / 2.0)
            .extrude(p.lid_mm + p.lid_lip_mm + 0.4)
        )
        lid = lid.cut(hole)
    return lid


def build(plan: EnclosurePlan) -> EnclosureMeshes:
    p = plan.params
    base_shape = _build_base(p).val()
    lid_shape = _build_lid(p).val()
    return EnclosureMeshes(base=_to_mesh(base_shape), lid=_to_mesh(lid_shape), params=p)
