"""build123d fallback engine.

Used only when CadQuery's OCP stack cannot be installed. The two engines
are never active in the same environment (build123d pulls the novtk OCP
build that breaks `import cadquery`).
"""

from __future__ import annotations

import numpy as np
from build123d import Align, Box, Cylinder, Pos

from sparkplan.geometry.engine import EnclosureMeshes, Mesh
from sparkplan.geometry.params import EnclosureParams, EnclosurePlan


def _to_mesh(obj) -> Mesh:
    verts, tris = obj.tessellate(0.05)
    v = np.asarray([[p.X, p.Y, p.Z] for p in verts], dtype=np.float64)
    f = np.asarray(tris, dtype=np.int64)
    return Mesh(vertices=v, faces=f)


def _boss_centers(p: EnclosureParams) -> list[tuple[float, float]]:
    """Boss centers in the centered coordinate frame of the outer box."""
    inset = p.wall_mm + p.boss_od_mm / 2.0 + 0.5
    return [
        (inset - p.outer_x_mm / 2.0, inset - p.outer_y_mm / 2.0),
        (p.outer_x_mm / 2.0 - inset, inset - p.outer_y_mm / 2.0),
        (inset - p.outer_x_mm / 2.0, p.outer_y_mm / 2.0 - inset),
        (p.outer_x_mm / 2.0 - inset, p.outer_y_mm / 2.0 - inset),
    ]


def _build_base(p: EnclosureParams):
    outer = Box(
        p.outer_x_mm, p.outer_y_mm, p.base_height_mm, align=(Align.CENTER, Align.CENTER, Align.MIN)
    )
    inner = Pos(0, 0, p.floor_mm) * Box(
        p.inner_x_mm,
        p.inner_y_mm,
        p.inner_z_mm + 1.0,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    body = outer - inner

    over = 0.4
    for cut in p.cutouts:
        if cut.wall not in ("-x", "+x"):
            continue
        y = cut.center_mm - p.inner_y_mm / 2.0
        z = cut.bottom_mm + cut.height_mm / 2.0
        x = -p.outer_x_mm / 2.0 if cut.wall == "-x" else p.outer_x_mm / 2.0
        box = Pos(x, y, z) * Box(
            p.wall_mm + over,
            cut.width_mm,
            cut.height_mm,
            align=(Align.CENTER, Align.CENTER, Align.CENTER),
        )
        body = body - box

    for cx, cy in _boss_centers(p):
        boss = Pos(cx, cy, p.floor_mm) * Cylinder(
            p.boss_od_mm / 2.0, p.inner_z_mm, align=(Align.CENTER, Align.CENTER, Align.MIN)
        )
        hole = Pos(cx, cy, p.floor_mm + p.inner_z_mm - p.boss_hole_mm) * Cylinder(
            p.boss_hole_mm / 2.0,
            p.boss_hole_mm + 0.4,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        body = body + boss - hole
    return body


def _build_lid(p: EnclosureParams):
    plate = Box(p.outer_x_mm, p.outer_y_mm, p.lid_mm, align=(Align.CENTER, Align.CENTER, Align.MIN))
    lip = Pos(0, 0, -p.lid_lip_mm) * Box(
        p.inner_x_mm - 0.4,
        p.inner_y_mm - 0.4,
        p.lid_lip_mm + 0.1,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    lid = plate + lip
    for cx, cy in _boss_centers(p):
        hole = Pos(cx, cy, -p.lid_lip_mm - 0.2) * Cylinder(
            p.boss_hole_mm / 2.0,
            p.lid_mm + p.lid_lip_mm + 0.4,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        lid = lid - hole
    return lid


def build(plan: EnclosurePlan) -> EnclosureMeshes:
    p = plan.params
    return EnclosureMeshes(base=_to_mesh(_build_base(p)), lid=_to_mesh(_build_lid(p)), params=p)
