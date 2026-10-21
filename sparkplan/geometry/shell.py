"""CadQuery shell builder for Phase 4 enclosures.

Turns an ``EnclosureLayout`` into printable solids: ``base`` (floor + walls +
corner posts), ``lid`` (plate + alignment lip + standoffs) and ``assembled``
(base + lid seated). Coordinate frames: the outer shell's min corner is the
origin; inner-frame coordinates map to it by ``+wall`` in x/y and ``+floor``
in z, as documented in ``sparkplan.geometry.enclosure``. The lid is built in
its own frame (plate spans ``0..lid_mm``, lip and bosses hang to negative z)
and only moves to its seated position in ``build_assembled``.

Construction order matters: cut openings first, then union standoff bosses
and corner posts (so a boss inside its own cutout survives, e.g. the HC-SR04
screw posts inside its sensor window), then cut pilot/insert holes.

CadQuery is imported at module level on purpose: ``generate_enclosure``
imports this module lazily and turns ``ImportError`` into a
``GEOMETRY_ENGINE_UNAVAILABLE`` warning.

No LLM, no network, no I/O. Units: millimetres.
"""

from __future__ import annotations

import cadquery as cq
import numpy as np

from sparkplan.geometry.enclosure import (
    EnclosureConfig,
    EnclosureLayout,
    FaceHole,
    Opening,
    Post,
)
from sparkplan.geometry.engine import Mesh

_OVER = 0.4  # cutouts overshoot both sides so faces cut cleanly
_BORE_OVER = 0.1  # hole cuts overshoot so they break through the solid
_BOSS_OD_EXTRA = 3.2  # boss wall around a pilot hole
_BOSS_PILOT_DEPTH = 3.0
_LIP_RING_WIDTH = 1.5
_LIP_POST_GAP = 0.5  # lip ring trimmed this much wider than a corner post


def _to_mesh(shape: cq.Shape, tol: float) -> Mesh:
    verts, tris = shape.tessellate(tol)
    v = [[p.x, p.y, p.z] for p in verts]
    f = [[t[0], t[1], t[2]] for t in tris]
    return Mesh(
        vertices=np.asarray(v, dtype=np.float64),
        faces=np.asarray(f, dtype=np.int64),
    )


def _rounded_box(x: float, y: float, z: float, cfg: EnclosureConfig) -> cq.Workplane:
    box = cq.Workplane("XY").box(x, y, z, centered=(False, False, False))
    r = cfg.corner_radius_mm
    if r > 0.0 and 2.0 * r + 0.2 < min(x, y):
        box = box.edges("|Z").fillet(r)
    return box


# ---------------------------------------------------------------------------
# cutouts (face frame -> solid cuts)
# ---------------------------------------------------------------------------


def _cut_shape(wp: cq.Workplane, o: Opening) -> cq.Workplane:
    if o.shape == "circle":
        return wp.circle(o.width_mm / 2.0)
    return wp.rect(o.width_mm, o.height_mm)


def _cut_wall(
    solid: cq.Workplane, o: Opening, cfg: EnclosureConfig, layout: EnclosureLayout
) -> cq.Workplane:
    """Cut a wall opening. ``cx`` = along-wall, ``cy`` = height above floor."""
    y = cfg.wall_mm + o.cx_mm
    z = cfg.floor_mm + o.cy_mm
    if o.face == "-x":
        wp = cq.Workplane("YZ", origin=(0.0, y, z))
        return solid.cut(_cut_shape(wp, o).extrude(cfg.wall_mm + _OVER, both=True))
    if o.face == "+x":
        wp = cq.Workplane("YZ", origin=(layout.outer_x_mm, y, z))
        return solid.cut(_cut_shape(wp, o).extrude(cfg.wall_mm + _OVER, both=True))
    x = cfg.wall_mm + o.cx_mm
    if o.face == "-y":
        wp = cq.Workplane("XZ", origin=(x, 0.0, z))
        return solid.cut(_cut_shape(wp, o).extrude(cfg.wall_mm + _OVER, both=True))
    if o.face == "+y":
        wp = cq.Workplane("XZ", origin=(x, layout.outer_y_mm, z))
        return solid.cut(_cut_shape(wp, o).extrude(cfg.wall_mm + _OVER, both=True))
    raise ValueError(f"not a wall face: {o.face}")


def _cut_floor(solid: cq.Workplane, o: Opening, cfg: EnclosureConfig) -> cq.Workplane:
    wp = cq.Workplane("XY", origin=(cfg.wall_mm + o.cx_mm, cfg.wall_mm + o.cy_mm, cfg.floor_mm))
    return solid.cut(_cut_shape(wp, o).extrude(-(cfg.floor_mm + _OVER)))


def _cut_lid_plate(solid: cq.Workplane, o: Opening, cfg: EnclosureConfig) -> cq.Workplane:
    """Lid-frame cut: plate spans 0..lid_mm, cut straight through it."""
    wp = cq.Workplane("XY", origin=(cfg.wall_mm + o.cx_mm, cfg.wall_mm + o.cy_mm, cfg.lid_mm / 2.0))
    return solid.cut(_cut_shape(wp, o).extrude(cfg.lid_mm / 2.0 + _OVER, both=True))


def _cut_opening(
    solid: cq.Workplane, o: Opening, cfg: EnclosureConfig, layout: EnclosureLayout
) -> cq.Workplane:
    if o.face in ("-x", "+x", "-y", "+y"):
        return _cut_wall(solid, o, cfg, layout)
    if o.face == "floor":
        return _cut_floor(solid, o, cfg)
    if o.face == "lid":
        return _cut_lid_plate(solid, o, cfg)
    raise ValueError(f"unknown face {o.face}")


def _vent_slots(o: Opening, cfg: EnclosureConfig) -> list[tuple[float, float, float, float]]:
    """Split a vent block into its individual slots (cx, cy, w, h)."""
    n = cfg.vent_count
    slot = cfg.vent_slot_mm
    gap = cfg.vent_gap_mm
    bv = n * slot + (n - 1) * gap
    slots: list[tuple[float, float, float, float]] = []
    if abs(o.height_mm - bv) < 1e-6:
        # stacked horizontal bars: thickness along v (height)
        for i in range(n):
            cv = o.cy_mm - o.height_mm / 2.0 + slot / 2.0 + i * (slot + gap)
            slots.append((o.cx_mm, cv, o.width_mm, slot))
    elif abs(o.width_mm - bv) < 1e-6:
        # side-by-side vertical bars: thickness along u (width)
        for i in range(n):
            cu = o.cx_mm - o.width_mm / 2.0 + slot / 2.0 + i * (slot + gap)
            slots.append((cu, o.cy_mm, slot, o.height_mm))
    else:
        slots.append((o.cx_mm, o.cy_mm, o.width_mm, o.height_mm))
    return slots


def _cut_vent(
    solid: cq.Workplane, o: Opening, cfg: EnclosureConfig, layout: EnclosureLayout
) -> cq.Workplane:
    for cx, cy, w, h in _vent_slots(o, cfg):
        slot_op = Opening(kind="vent", face=o.face, cx_mm=cx, cy_mm=cy, width_mm=w, height_mm=h)
        solid = _cut_opening(solid, slot_op, cfg, layout)
    return solid


# ---------------------------------------------------------------------------
# standoff bosses
# ---------------------------------------------------------------------------


def _pilot_diameter(diameter: float) -> float:
    return max(1.2, diameter * 0.6)


def _floor_bosses(solid: cq.Workplane, holes: list[FaceHole], cfg: EnclosureConfig) -> cq.Workplane:
    for hole in holes:
        od = hole.diameter + _BOSS_OD_EXTRA
        boss = (
            cq.Workplane(
                "XY",
                origin=(
                    cfg.wall_mm + hole.u_mm,
                    cfg.wall_mm + hole.v_mm,
                    cfg.floor_mm,
                ),
            )
            .circle(od / 2.0)
            .extrude(cfg.standoff_height_mm)
        )
        solid = solid.union(boss)
    return solid


def _lid_bosses(solid: cq.Workplane, holes: list[FaceHole], cfg: EnclosureConfig) -> cq.Workplane:
    for hole in holes:
        od = hole.diameter + _BOSS_OD_EXTRA
        boss = (
            cq.Workplane(
                "XY",
                origin=(
                    cfg.wall_mm + hole.u_mm,
                    cfg.wall_mm + hole.v_mm,
                    -cfg.standoff_height_mm,
                ),
            )
            .circle(od / 2.0)
            .extrude(cfg.standoff_height_mm)
        )
        solid = solid.union(boss)
    return solid


def _wall_boss_axis(
    hole: FaceHole, cfg: EnclosureConfig, layout: EnclosureLayout
) -> tuple[tuple[float, float, float], tuple[int, int, int]]:
    """Boss origin (on the wall inner face) and axis pointing into the cavity."""
    z = cfg.floor_mm + hole.v_mm
    if hole.face == "-x":
        return (cfg.wall_mm, cfg.wall_mm + hole.u_mm, z), (1, 0, 0)
    if hole.face == "+x":
        return (layout.outer_x_mm - cfg.wall_mm, cfg.wall_mm + hole.u_mm, z), (-1, 0, 0)
    if hole.face == "-y":
        return (cfg.wall_mm + hole.u_mm, cfg.wall_mm, z), (0, 1, 0)
    return (cfg.wall_mm + hole.u_mm, layout.outer_y_mm - cfg.wall_mm, z), (0, -1, 0)


def _wp_at(
    origin: tuple[float, float, float], axis: tuple[int, int, int], plane: str
) -> cq.Workplane:
    """Workplane on a wall plane whose extrude runs along ``axis``."""
    normal = (0, 0, 0)
    if plane == "YZ":
        normal = (1, 0, 0)
    elif plane == "XZ":
        normal = (0, -1, 0)
    if axis == normal:
        return cq.Workplane(plane, origin=origin)
    return cq.Workplane(plane, origin=origin, normal=axis)


def _wall_bosses(
    solid: cq.Workplane, holes: list[FaceHole], cfg: EnclosureConfig, layout: EnclosureLayout
) -> cq.Workplane:
    for hole in holes:
        origin, axis = _wall_boss_axis(hole, cfg, layout)
        plane = "YZ" if hole.face in ("-x", "+x") else "XZ"
        od = hole.diameter + _BOSS_OD_EXTRA
        wp = _wp_at(origin, axis, plane)
        solid = solid.union(wp.circle(od / 2.0).extrude(cfg.standoff_height_mm))
    return solid


def _pilot_cut(
    solid: cq.Workplane, hole: FaceHole, cfg: EnclosureConfig, layout: EnclosureLayout, *, lid: bool
) -> cq.Workplane:
    """Blind pilot hole (3 mm) from the free end of a standoff boss."""
    depth = _BOSS_PILOT_DEPTH
    d = _pilot_diameter(hole.diameter)
    if lid:
        wp = cq.Workplane(
            "XY",
            origin=(
                cfg.wall_mm + hole.u_mm,
                cfg.wall_mm + hole.v_mm,
                -cfg.standoff_height_mm,
            ),
        )
        return solid.cut(wp.circle(d / 2.0).extrude(depth + _BORE_OVER))
    if hole.face == "floor":
        wp = cq.Workplane(
            "XY",
            origin=(
                cfg.wall_mm + hole.u_mm,
                cfg.wall_mm + hole.v_mm,
                cfg.floor_mm + cfg.standoff_height_mm - depth,
            ),
        )
        return solid.cut(wp.circle(d / 2.0).extrude(depth + _BORE_OVER))
    origin, axis = _wall_boss_axis(hole, cfg, layout)
    origin = (
        origin[0] + axis[0] * cfg.standoff_height_mm,
        origin[1] + axis[1] * cfg.standoff_height_mm,
        origin[2] + axis[2] * cfg.standoff_height_mm,
    )
    plane = "YZ" if hole.face in ("-x", "+x") else "XZ"
    back = tuple(-a for a in axis)
    wp = _wp_at(origin, back, plane)  # type: ignore[arg-type]
    return solid.cut(wp.circle(d / 2.0).extrude(depth + _BORE_OVER))


# ---------------------------------------------------------------------------
# corner posts
# ---------------------------------------------------------------------------


def _post_positions(layout: EnclosureLayout) -> list[tuple[float, float, Post]]:
    cfg = layout.config
    return [(cfg.wall_mm + p.x_mm, cfg.wall_mm + p.y_mm, p) for p in layout.posts]


def _insert_depth(post: Post, inner_z: float) -> float:
    return min(6.0, inner_z - 1.0)


# ---------------------------------------------------------------------------
# public builders
# ---------------------------------------------------------------------------


def _all_holes(layout: EnclosureLayout) -> list[FaceHole]:
    return [h for part in layout.parts for h in part.standoff_holes]


def build_base(layout: EnclosureLayout) -> cq.Workplane:
    """Floor + four walls + corner posts + bosses, open on top."""
    cfg = layout.config
    total_z = cfg.floor_mm + layout.inner_z_mm

    solid = _rounded_box(layout.outer_x_mm, layout.outer_y_mm, total_z, cfg)
    cavity = cq.Workplane("XY", origin=(cfg.wall_mm, cfg.wall_mm, cfg.floor_mm)).box(
        layout.inner_x_mm,
        layout.inner_y_mm,
        layout.inner_z_mm + 1.0,
        centered=(False, False, False),
    )
    solid = solid.cut(cavity)

    for o in layout.openings:
        if o.face == "lid":
            continue  # lid cutouts belong to the lid solid
        if o.kind == "vent":
            solid = _cut_vent(solid, o, cfg, layout)
        else:
            solid = _cut_opening(solid, o, cfg, layout)

    floor_holes = [h for h in _all_holes(layout) if h.face == "floor"]
    wall_holes = [h for h in _all_holes(layout) if h.face in ("-x", "+x", "-y", "+y")]

    # bosses union after cutouts, then cut pilots
    solid = _floor_bosses(solid, floor_holes, cfg)
    solid = _wall_bosses(solid, wall_holes, cfg, layout)
    for hole in floor_holes + wall_holes:
        solid = _pilot_cut(solid, hole, cfg, layout, lid=False)

    # corner posts: full inner height, unioned so no cutout clips them
    for px, py, post in _post_positions(layout):
        boss = (
            cq.Workplane("XY", origin=(px, py, cfg.floor_mm))
            .circle(post.od_mm / 2.0)
            .extrude(layout.inner_z_mm)
        )
        solid = solid.union(boss)

    # brass insert holes in post tops (blind from the top face)
    for px, py, post in _post_positions(layout):
        depth = _insert_depth(post, layout.inner_z_mm)
        hole = (
            cq.Workplane("XY", origin=(px, py, cfg.floor_mm + layout.inner_z_mm - depth))
            .circle(post.hole_mm / 2.0)
            .extrude(depth + 1.0)
        )
        solid = solid.cut(hole)
    return solid


def build_lid(layout: EnclosureLayout) -> cq.Workplane:
    """Plate + alignment lip + standoff bosses, built in the lid frame."""
    cfg = layout.config
    solid = _rounded_box(layout.outer_x_mm, layout.outer_y_mm, cfg.lid_mm, cfg)

    # alignment lip: ring just inside the cavity, corners trimmed clear of the
    # corner posts so the lip seats without fouling them
    ring_w = _LIP_RING_WIDTH
    lip_ox = layout.inner_x_mm - 2.0 * cfg.lid_lip_gap_mm
    lip_oy = layout.inner_y_mm - 2.0 * cfg.lid_lip_gap_mm
    lip = cq.Workplane(
        "XY",
        origin=(
            cfg.wall_mm + cfg.lid_lip_gap_mm,
            cfg.wall_mm + cfg.lid_lip_gap_mm,
            -cfg.lid_lip_mm,
        ),
    ).box(lip_ox, lip_oy, cfg.lid_lip_mm, centered=(False, False, False))
    lip = lip.cut(
        cq.Workplane(
            "XY",
            origin=(
                cfg.wall_mm + cfg.lid_lip_gap_mm + ring_w,
                cfg.wall_mm + cfg.lid_lip_gap_mm + ring_w,
                -cfg.lid_lip_mm - _BORE_OVER,
            ),
        ).box(
            lip_ox - 2.0 * ring_w,
            lip_oy - 2.0 * ring_w,
            cfg.lid_lip_mm + 2.0 * _BORE_OVER,
            centered=(False, False, False),
        )
    )
    for px, py, post in _post_positions(layout):
        lip = lip.cut(
            cq.Workplane("XY", origin=(px, py, -cfg.lid_lip_mm - _BORE_OVER))
            .circle((post.od_mm + _LIP_POST_GAP) / 2.0)
            .extrude(cfg.lid_lip_mm + 2.0 * _BORE_OVER)
        )
    solid = solid.union(lip)

    for o in layout.openings:
        if o.face != "lid":
            continue
        if o.kind == "vent":
            solid = _cut_vent(solid, o, cfg, layout)
        else:
            solid = _cut_opening(solid, o, cfg, layout)

    lid_holes = [h for h in _all_holes(layout) if h.face == "lid"]
    solid = _lid_bosses(solid, lid_holes, cfg)
    for hole in lid_holes:
        solid = _pilot_cut(solid, hole, cfg, layout, lid=True)

    # screw clearance through plate (+ lip remnants) at the corner posts
    for px, py, _post in _post_positions(layout):
        hole = (
            cq.Workplane("XY", origin=(px, py, -cfg.lid_lip_mm - 2.0 * _BORE_OVER))
            .circle(cfg.lid_screw_hole_mm / 2.0)
            .extrude(cfg.lid_lip_mm + cfg.lid_mm + 4.0 * _BORE_OVER)
        )
        solid = solid.cut(hole)
    return solid


def build_assembled(layout: EnclosureLayout) -> cq.Workplane:
    """Base with the lid seated on top (lid lip hanging inside the cavity)."""
    cfg = layout.config
    base = build_base(layout)
    lid = build_lid(layout)
    return base.union(lid.translate((0.0, 0.0, cfg.floor_mm + layout.inner_z_mm)))


def meshes(layout: EnclosureLayout) -> tuple[Mesh, Mesh, Mesh]:
    """Tessellate base, lid and assembled solids for STL export."""
    tol = layout.config.tessellation_mm
    return (
        _to_mesh(build_base(layout).val(), tol),
        _to_mesh(build_lid(layout).val(), tol),
        _to_mesh(build_assembled(layout).val(), tol),
    )
