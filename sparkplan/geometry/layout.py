"""Deterministic enclosure layout (Phase 4).

Packs a validated ``SupportedBuild`` against the Phase 1 catalog into an
``EnclosureLayout``: shelf packing with 90 degree rotation for base parts,
wall placement with the exposure centred on the assigned wall, lid parts
hanging from the underside, the ESP32 pinned with its USB end against a wall,
and the inner height derived from the base and lid stacks.

No LLM, no network, no I/O. Failures come back as ``Issue`` objects with fix
hints. Units: millimetres.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sparkplan.geometry.enclosure import (
    WALL_FACE_ORDER,
    WALL_FACES,
    EnclosureConfig,
    EnclosureLayout,
    FaceHole,
    Opening,
    PackedPart,
    Post,
)
from sparkplan.models.buildspec import SupportedBuild
from sparkplan.models.catalog import (
    Catalog,
    Category,
    ExposureFace,
    Mount,
    Part,
    get_catalog,
)
from sparkplan.models.spec import Issue

_EPS = 1e-9

_FACE_NORMALS: dict[str, tuple[int, int]] = {
    "front": (0, -1),
    "back": (0, 1),
    "left": (-1, 0),
    "right": (1, 0),
}
_WALL_NORMALS: dict[str, tuple[int, int]] = {
    "-x": (-1, 0),
    "+x": (1, 0),
    "-y": (0, -1),
    "+y": (0, 1),
}
_SIDE_FACES: frozenset[str] = frozenset(_FACE_NORMALS)


def _issue(
    code: str,
    message: str,
    *,
    severity: str = "error",
    part_ids: list[str] | None = None,
    fix_hint: str = "",
) -> Issue:
    return Issue(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        message=message,
        part_ids=part_ids or [],
        fix_hint=fix_hint,
    )


def _rot(x: float, y: float, deg: int) -> tuple[float, float]:
    if deg == 0:
        return x, y
    if deg == 90:
        return -y, x
    if deg == 180:
        return -x, -y
    if deg == 270:
        return y, -x
    raise ValueError(f"unsupported rotation {deg}")


def _rotation_for_face(face: str, wall: str) -> int:
    fv = _FACE_NORMALS[face]
    wv = _WALL_NORMALS[wall]
    for deg in (0, 90, 180, 270):
        rx, ry = _rot(fv[0], fv[1], deg)
        if abs(rx - wv[0]) < _EPS and abs(ry - wv[1]) < _EPS:
            return deg
    raise ValueError(f"no rotation maps {face} onto {wall}")


def _side_face(exposure_face: ExposureFace) -> str | None:
    value = exposure_face.value
    return value if value in _SIDE_FACES else None


@dataclass
class _Item:
    instance_id: str
    part: Part
    mount: Mount
    rotation: int = 0
    # plan AABB in the inner frame (min corner + extents)
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    sx: float = 0.0
    sy: float = 0.0
    sz: float = 0.0
    # wall / pinned metrics
    wall: str | None = None
    h: float = 0.0  # extent along the assigned wall
    depth: float = 0.0  # extent normal to the wall (incl. horizontal standoff)
    u0: float = 0.0  # along-wall start of the part
    protrudes: float = 0.0  # prong length outside the shell (pass-through)
    holes: list[FaceHole] = field(default_factory=list)

    @property
    def is_board(self) -> bool:
        return self.part.category is Category.BOARD

    @property
    def footprint(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.x + self.sx, self.y + self.sy)

    @property
    def has_side_exposure(self) -> bool:
        return _side_face(self.part.exposure.face) is not None

    @property
    def is_pass_through(self) -> bool:
        """Wall part whose long axis exits through the floor or lid.

        The catalog models parts like the soil probe (20 x 98 x 2 mm, wall
        mount, bottom exposure) as a head enclosed inside plus a prong that
        sticks out through a slot; only the head occupies the cavity.
        """
        return self.mount is Mount.WALL and self.part.exposure.face in (
            ExposureFace.TOP,
            ExposureFace.BOTTOM,
        )


# ---------------------------------------------------------------------------
# resolution helpers
# ---------------------------------------------------------------------------


def _mount_for(spec: SupportedBuild, part: Part) -> Mount:
    for hint in spec.layout:
        if hint.part_id == part.id:
            return Mount(hint.mount)
    return part.mount


def _resolve_items(spec: SupportedBuild, catalog: Catalog, issues: list[Issue]) -> list[_Item]:
    items: list[_Item] = []
    for ref in spec.parts:
        part = catalog.get_or_none(ref.part_id)
        if part is None:
            issues.append(
                _issue(
                    "GEOMETRY_UNKNOWN_PART",
                    f"Part id {ref.part_id!r} is not in the catalog, so its "
                    "dimensions are unknown and it cannot be placed.",
                    part_ids=[ref.part_id],
                    fix_hint="Use only part ids from catalog/parts.json.",
                )
            )
            continue
        mount = _mount_for(spec, part)
        for n in range(ref.quantity):
            iid = ref.instance_id if n == 0 else f"{ref.instance_id}_{n + 1}"
            items.append(_Item(instance_id=iid, part=part, mount=mount))
    return items


def _pack_order(items: list[_Item]) -> list[_Item]:
    return sorted(
        items,
        key=lambda i: (
            -(i.part.dimensions_mm.x * i.part.dimensions_mm.y),
            i.instance_id,
        ),
    )


def _open_w(item: _Item) -> float:
    return item.part.exposure.opening_x_mm


def _open_h(item: _Item) -> float:
    return item.part.exposure.opening_y_mm


def _has_holes(item: _Item) -> bool:
    return bool(item.part.mounting_holes)


# ---------------------------------------------------------------------------
# board / wall orientation and assignment
# ---------------------------------------------------------------------------


def _pin_to_wall(item: _Item, cfg: EnclosureConfig, issues: list[Issue]) -> bool:
    """Pin a base-mounted part with a side exposure against an outer wall."""
    face = _side_face(item.part.exposure.face) or "front"
    d = item.part.dimensions_mm
    keep = cfg.post_keepout_mm
    for wall in ("-x", "+x", "-y", "+y"):
        deg = _rotation_for_face(face, wall)
        ex, ey = (d.y, d.x) if deg in (90, 270) else (d.x, d.y)
        if wall in ("-x", "+x"):
            along, depth, along_max = ey, ex, cfg.inner_max_y_mm
            normal_max = cfg.inner_max_x_mm
        else:
            along, depth, along_max = ex, ey, cfg.inner_max_x_mm
            normal_max = cfg.inner_max_y_mm
        if along + 2.0 * keep <= along_max + _EPS and depth <= normal_max + _EPS:
            item.wall = wall
            item.rotation = deg
            item.h = along
            item.depth = depth  # flat part against the wall: no horizontal boss
            item.sx, item.sy = ex, ey
            item.sz = d.z
            return True
    issues.append(
        _issue(
            "GEOMETRY_PART_TOO_LARGE",
            (
                f"{item.instance_id} ({item.part.name}) needs "
                f"{d.x:.1f} x {d.y:.1f} mm on a wall but no wall of the "
                f"{cfg.build_x_mm:.0f} x {cfg.build_y_mm:.0f} x "
                f"{cfg.build_z_mm:.0f} mm build volume is large enough "
                f"including {cfg.post_keepout_mm:.1f} mm screw-post clearance "
                "on both ends."
            ),
            part_ids=[item.instance_id],
            fix_hint=(
                "Remove or replace this part, or raise the build volume in " "EnclosureConfig."
            ),
        )
    )
    return False


def _orient_wall_item(item: _Item, cfg: EnclosureConfig) -> None:
    """Choose the 0/90 orientation that best fits the exposure on the wall."""
    d = item.part.dimensions_mm
    exp = item.part.exposure
    if item.is_pass_through or exp.face is ExposureFace.NONE:
        # no opening on this wall: put the shorter plan dimension along the
        # wall so the long axis (the prong) runs vertically through the slot
        if d.x <= d.y:
            item.rotation = 0
            item.h, item.sz = d.x, d.y
        else:
            item.rotation = 90
            item.h, item.sz = d.y, d.x
    else:
        open_w, open_h = exp.opening_x_mm, exp.opening_y_mm
        score_a = min(d.x - open_w, d.y - open_h)
        score_b = min(d.y - open_w, d.x - open_h)
        if score_b > score_a + _EPS:
            item.rotation = 90
            item.h, item.sz = d.y, d.x
        else:
            item.rotation = 0
            item.h, item.sz = d.x, d.y
    standoff = cfg.standoff_height_mm if _has_holes(item) else 0.0
    item.depth = d.z + standoff


def _eff_len(item: _Item, cfg: EnclosureConfig) -> float:
    """Along-wall space an item needs, including clearance and its cutout."""
    open_extent = 0.0
    if item.part.exposure.face is not ExposureFace.NONE and not item.is_pass_through:
        # pass-through cutouts live on the floor/lid, not on this wall
        open_extent = _open_w(item) + cfg.cutout_tolerance_mm
    return max(item.h + cfg.clearance_mm, open_extent)


def _assign_walls(wall_items: list[_Item], cfg: EnclosureConfig, issues: list[Issue]) -> bool:
    ok = True
    keep = cfg.post_keepout_mm
    for idx, item in enumerate(wall_items):
        placed = False
        for k in range(len(WALL_FACE_ORDER)):
            wall = WALL_FACE_ORDER[(idx + k) % len(WALL_FACE_ORDER)]
            along_max = cfg.inner_max_y_mm if wall in ("-x", "+x") else cfg.inner_max_x_mm
            if _eff_len(item, cfg) + 2.0 * keep <= along_max + _EPS:
                item.wall = wall
                placed = True
                break
        if not placed:
            ok = False
            issues.append(
                _issue(
                    "GEOMETRY_PART_TOO_LARGE",
                    (
                        f"{item.instance_id} ({item.part.name}) needs "
                        f"{_eff_len(item, cfg):.1f} mm along a wall but the "
                        f"largest usable wall run is "
                        f"{min(cfg.inner_max_x_mm, cfg.inner_max_y_mm) - 2 * keep:.1f} mm "
                        f"after {keep:.1f} mm screw-post clearance at both ends."
                    ),
                    part_ids=[item.instance_id],
                    fix_hint=(
                        "Move this part to another mount zone, drop it from "
                        "the build, or raise the build volume in "
                        "EnclosureConfig."
                    ),
                )
            )
    return ok


# ---------------------------------------------------------------------------
# shelf packing
# ---------------------------------------------------------------------------


def _shelf_pack(
    items: list[_Item],
    cfg: EnclosureConfig,
    *,
    x0: float,
    y0: float,
    max_w: float,
    max_h: float,
    issues: list[Issue],
) -> tuple[float, float]:
    """First-fit shelf packing with 90 degree rotation. Returns extents."""
    clr = cfg.clearance_mm
    row_y = y0
    row_w = 0.0
    row_depth = 0.0
    ext_x = 0.0
    for item in items:
        d = item.part.dimensions_mm
        opts: list[tuple[float, float, int]] = [(d.x, d.y, 0)]
        if abs(d.x - d.y) > _EPS:
            opts.append((d.y, d.x, 90))
        in_row = [o for o in opts if row_w + o[0] <= max_w + _EPS]
        if row_w > 0.0 and in_row:
            w, h, rot = in_row[0]
            px, py = x0 + row_w, row_y
        else:
            fits = [o for o in opts if o[0] <= max_w + _EPS]
            if not fits:
                issues.append(
                    _issue(
                        "GEOMETRY_PART_TOO_LARGE",
                        (
                            f"{item.instance_id} ({item.part.name}) is "
                            f"{d.x:.1f} x {d.y:.1f} mm but only "
                            f"{max_w:.1f} mm of packing width is available "
                            f"inside the {cfg.build_x_mm:.0f} x "
                            f"{cfg.build_y_mm:.0f} mm build volume after "
                            f"walls, clearance and screw posts."
                        ),
                        part_ids=[item.instance_id],
                        fix_hint=(
                            "Remove this part, replace it with a smaller "
                            "catalog part, or raise the build volume in "
                            "EnclosureConfig."
                        ),
                    )
                )
                return ext_x, (row_y + row_depth) - y0
            if row_w > 0.0:
                row_y += row_depth + clr
                row_w = 0.0
                row_depth = 0.0
            w, h, rot = fits[0]
            px, py = x0, row_y
            if py + h > y0 + max_h + _EPS:
                issues.append(
                    _issue(
                        "GEOMETRY_LAYOUT_TOO_LARGE",
                        (
                            f"The parts do not fit the build volume: after "
                            f"{row_y - y0 + h:.1f} mm of packed rows only "
                            f"{max_h:.1f} mm of depth remains inside the "
                            f"{cfg.build_x_mm:.0f} x {cfg.build_y_mm:.0f} mm "
                            "footprint."
                        ),
                        part_ids=[item.instance_id],
                        fix_hint=(
                            "Use fewer or smaller parts, move some to another "
                            "mount zone, or raise the build volume."
                        ),
                    )
                )
                return ext_x, (row_y + row_depth) - y0
        item.x, item.y = px, py
        item.rotation = rot
        item.sx, item.sy = item_size(item, rot)
        item.sz = item.part.dimensions_mm.z
        row_w += w + clr
        row_depth = max(row_depth, h)
        ext_x = max(ext_x, px + w - x0)
    ext_y = (row_y + row_depth) - y0
    return ext_x, ext_y


def item_size(item: _Item, rot: int) -> tuple[float, float]:
    d = item.part.dimensions_mm
    return (d.y, d.x) if rot in (90, 270) else (d.x, d.y)


# ---------------------------------------------------------------------------
# wall placement (segment allocation along a wall)
# ---------------------------------------------------------------------------


def _place_on_wall(
    group: list[_Item],
    wall: str,
    lo: float,
    hi: float,
    cfg: EnclosureConfig,
    issues: list[Issue],
) -> bool:
    """Place items along one wall: first centred, later ones in free gaps."""
    clr = cfg.clearance_mm
    free = [(lo, hi)]
    ok = True
    for item in group:
        need = item.h
        best: tuple[float, float] | None = None
        for g0, g1 in free:
            if g1 - g0 >= need - _EPS and (best is None or g1 - g0 > best[1] - best[0]):
                best = (g0, g1)
        if best is None:
            ok = False
            issues.append(
                _issue(
                    "GEOMETRY_WALL_FULL",
                    (
                        f"{item.instance_id} ({item.part.name}) needs "
                        f"{need:.1f} mm along the {wall} wall but no free "
                        f"segment of {hi - lo:.1f} mm remains after the other "
                        "parts and screw-post clearance."
                    ),
                    part_ids=[item.instance_id],
                    fix_hint=(
                        "Move this part to a different wall by changing its "
                        "layout hint, or remove a part from this wall."
                    ),
                )
            )
            continue
        g0, g1 = best
        pos = g0 + (g1 - g0 - need) / 2.0
        item.u0 = pos
        b0, b1 = pos - clr, pos + need + clr
        nxt: list[tuple[float, float]] = []
        for a, b in free:
            if b <= b0 + _EPS or a >= b1 - _EPS:
                nxt.append((a, b))
                continue
            if a < b0 - _EPS:
                nxt.append((a, b0))
            if b1 < b - _EPS:
                nxt.append((b1, b))
        free = nxt
    return ok


def _apply_wall_plan(item: _Item, wall: str, inner_x: float, inner_y: float) -> None:
    """Turn an item's along-wall position into an inner-frame AABB."""
    standoff = item.depth - (item.part.dimensions_mm.z if item.mount is Mount.WALL else 0.0)
    if item.mount is Mount.WALL:
        thickness = item.part.dimensions_mm.z
        n_min = standoff  # horizontal standoff keeps the wall part off the wall
    else:
        thickness = item.depth  # flat part against the wall: depth is plan
        n_min = 0.0
    if wall in ("-x", "+x"):
        item.sx, item.sy = thickness, item.h
        item.y = item.u0
        item.x = n_min if wall == "-x" else inner_x - n_min - thickness
    else:
        item.sx, item.sy = item.h, thickness
        item.x = item.u0
        item.y = n_min if wall == "-y" else inner_y - n_min - thickness


# ---------------------------------------------------------------------------
# holes -> face coordinates
# ---------------------------------------------------------------------------


def _base_holes(item: _Item, face: str) -> list[FaceHole]:
    d = item.part.dimensions_mm
    out: list[FaceHole] = []
    for hole in item.part.mounting_holes:
        rx, ry = hole.x - d.x / 2.0, hole.y - d.y / 2.0
        ox, oy = _rot(rx, ry, item.rotation)
        out.append(
            FaceHole(
                face=face,  # type: ignore[arg-type]
                u_mm=item.x + item.sx / 2.0 + ox,
                v_mm=item.y + item.sy / 2.0 + oy,
                diameter=hole.diameter,
            )
        )
    return out


def _wall_holes(item: _Item) -> list[FaceHole]:
    out: list[FaceHole] = []
    for hole in item.part.mounting_holes:
        if item.rotation == 90:
            u = item.u0 + hole.y
            v = hole.x
        else:
            u = item.u0 + hole.x
            v = hole.y
        out.append(
            FaceHole(
                face=item.wall,  # type: ignore[arg-type]
                u_mm=u,
                v_mm=v,
                diameter=hole.diameter,
            )
        )
    return out


# ---------------------------------------------------------------------------
# openings
# ---------------------------------------------------------------------------


def _cutout_size(item: _Item, cfg: EnclosureConfig) -> tuple[float, float, str]:
    exp = item.part.exposure
    tol = cfg.cutout_tolerance_mm
    w, h = exp.opening_x_mm + tol, exp.opening_y_mm + tol
    shape = "circle" if abs(exp.opening_x_mm - exp.opening_y_mm) <= _EPS else "rect"
    return w, h, shape


def _plan_opening_centre(item: _Item) -> tuple[float, float]:
    exp = item.part.exposure
    cx = item.x + item.sx / 2.0
    cy = item.y + item.sy / 2.0
    ox, oy = _rot(exp.offset_x_mm, exp.offset_y_mm, item.rotation)
    return cx + ox, cy + oy


def _item_opening(
    item: _Item, cfg: EnclosureConfig, inner_x: float, inner_y: float, inner_z: float
) -> Opening | None:
    exp = item.part.exposure
    if exp.face is ExposureFace.NONE:
        return None
    w, h, shape = _cutout_size(item, cfg)
    face_value = exp.face.value

    if item.is_pass_through and item.wall is not None:
        # slot in the floor (or lid) for the prong; opening_x runs along the
        # wall, opening_y across it, clamped so the slot stays on the face
        face = "floor" if exp.face is ExposureFace.BOTTOM else "lid"
        u_off = exp.offset_y_mm if item.rotation == 90 else exp.offset_x_mm
        along = item.u0 + item.h / 2.0 + u_off
        across = item.depth / 2.0  # part centre in the wall-normal direction
        span = inner_x if item.wall in ("-x", "+x") else inner_y
        across = min(max(across, h / 2.0), span - h / 2.0)
        if item.wall in ("-x", "+x"):
            px_mm = across if item.wall == "-x" else span - across
            py_mm = along
            w_plan, h_plan = h, w
        else:
            px_mm = along
            py_mm = across if item.wall == "-y" else span - across
            w_plan, h_plan = w, h
        return Opening(
            face=face,  # type: ignore[arg-type]
            cx_mm=px_mm,
            cy_mm=py_mm,
            width_mm=w_plan,
            height_mm=h_plan,
            shape=shape,  # type: ignore[arg-type]
            parts=[item.instance_id],
        )

    if item.mount is Mount.WALL and item.wall is not None:
        if item.rotation == 90:
            u_off, v_off = exp.offset_y_mm, exp.offset_x_mm
        else:
            u_off, v_off = exp.offset_x_mm, exp.offset_y_mm
        cx = item.u0 + item.h / 2.0 + u_off
        cy = item.sz / 2.0 + v_off
        if h <= inner_z:
            cy = min(max(cy, h / 2.0), inner_z - h / 2.0)
        return Opening(
            face=item.wall,  # type: ignore[arg-type]
            cx_mm=cx,
            cy_mm=cy,
            width_mm=w,
            height_mm=h,
            shape=shape,  # type: ignore[arg-type]
            parts=[item.instance_id],
        )

    px, py = _plan_opening_centre(item)
    if face_value in _SIDE_FACES and item.wall is not None:
        deg = _rotation_for_face(face_value, item.wall)
        rx, ry = _rot(exp.offset_x_mm, exp.offset_y_mm, deg)
        if item.mount is Mount.WALL:  # pragma: no cover - handled above
            return None
        cx_plan = item.x + item.sx / 2.0 + rx
        cy_plan = item.y + item.sy / 2.0 + ry
        cx = cy_plan if item.wall in ("-x", "+x") else cx_plan
        return Opening(
            face=item.wall,  # type: ignore[arg-type]
            cx_mm=cx,
            cy_mm=item.z + item.sz / 2.0,
            width_mm=w,
            height_mm=h,
            shape=shape,  # type: ignore[arg-type]
            parts=[item.instance_id],
        )

    if face_value == "top":
        return Opening(
            face="lid",
            cx_mm=px,
            cy_mm=py,
            width_mm=w,
            height_mm=h,
            shape=shape,  # type: ignore[arg-type]
            parts=[item.instance_id],
        )
    if face_value == "bottom":
        return Opening(
            face="floor",
            cx_mm=px,
            cy_mm=py,
            width_mm=w,
            height_mm=h,
            shape=shape,  # type: ignore[arg-type]
            parts=[item.instance_id],
        )
    return None


# ---------------------------------------------------------------------------
# vents
# ---------------------------------------------------------------------------


def _face_bounds(
    face: str, cfg: EnclosureConfig, inner_x: float, inner_y: float, inner_z: float
) -> tuple[float, float]:
    if face in ("-x", "+x"):
        return 0.0, inner_y
    if face in ("-y", "+y"):
        return 0.0, inner_x
    return inner_x, inner_y


def _post_hit(
    face: str,
    cx: float,
    cy: float,
    w: float,
    h: float,
    cfg: EnclosureConfig,
    posts: list[Post],
) -> bool:
    r = cfg.post_od_mm / 2.0
    # project posts onto the face and test rectangle-vs-(disc | full strip)
    if face in ("-x", "+x"):
        # posts adjacent to an x-wall are those at either end in y
        us = sorted({round(p.y_mm, 6) for p in posts})
        return any(abs(cx - u) < w / 2.0 + r - _EPS for u in us)
    if face in ("-y", "+y"):
        us = sorted({round(p.x_mm, 6) for p in posts})
        return any(abs(cx - u) < w / 2.0 + r - _EPS for u in us)
    for p in posts:
        if abs(cx - p.x_mm) < w / 2.0 + r - _EPS and abs(cy - p.y_mm) < h / 2.0 + r - _EPS:
            return True
    return False


def _rect_overlaps(
    ax: float,
    ay: float,
    aw: float,
    ah: float,
    bx: float,
    by: float,
    bw: float,
    bh: float,
    margin: float,
) -> bool:
    return (
        abs(ax - bx) < (aw + bw) / 2.0 + margin - _EPS
        and abs(ay - by) < (ah + bh) / 2.0 + margin - _EPS
    )


def _vent_for(
    item: _Item,
    cfg: EnclosureConfig,
    layout_openings: list[Opening],
    posts: list[Post],
    inner_x: float,
    inner_y: float,
    inner_z: float,
    issues: list[Issue],
) -> None:
    """Find a vent block next to a heat source, or record a warning."""
    if item.mount is Mount.WALL and item.wall is not None:
        face = item.wall
        anchor = next(
            (
                o
                for o in layout_openings
                if o.kind == "exposure" and o.face == face and item.instance_id in o.parts
            ),
            None,
        )
        if anchor is None:
            anchor = Opening(
                face=face,  # type: ignore[arg-type]
                cx_mm=item.u0 + item.h / 2.0,
                cy_mm=item.sz / 2.0,
                width_mm=item.h,
                height_mm=item.sz,
                parts=[item.instance_id],
            )
    else:
        face = "lid"
        anchor = next(
            (
                o
                for o in layout_openings
                if o.kind == "exposure" and o.face == "lid" and item.instance_id in o.parts
            ),
            None,
        )
        if anchor is None:
            anchor = Opening(
                face="lid",
                cx_mm=item.x + item.sx / 2.0,
                cy_mm=item.y + item.sy / 2.0,
                width_mm=item.sx,
                height_mm=item.sy,
                parts=[item.instance_id],
            )

    bu = cfg.vent_slot_length_mm
    bv = cfg.vent_count * cfg.vent_slot_mm + (cfg.vent_count - 1) * cfg.vent_gap_mm
    m = cfg.vent_margin_mm
    lo_u, hi_u = _face_bounds(face, cfg, inner_x, inner_y, inner_z)
    # candidate centres: above, below, right, left of the anchor
    candidates = [
        (anchor.cx_mm, anchor.cy_mm + anchor.height_mm / 2.0 + m + bv / 2.0),
        (anchor.cx_mm, anchor.cy_mm - anchor.height_mm / 2.0 - m - bv / 2.0),
        (anchor.cx_mm + anchor.width_mm / 2.0 + m + bu / 2.0, anchor.cy_mm),
        (anchor.cx_mm - anchor.width_mm / 2.0 - m - bu / 2.0, anchor.cy_mm),
    ]
    if face in ("-x", "+x", "-y", "+y"):
        # walls: u runs along the wall, v is vertical (always inner_z)
        u_lo, u_hi = cfg.cutout_post_keepout_mm, lo_u - cfg.cutout_post_keepout_mm
        v_lo, v_hi = m, inner_z - m
    else:
        u_lo, u_hi = cfg.cutout_post_keepout_mm, lo_u - cfg.cutout_post_keepout_mm
        v_lo, v_hi = cfg.cutout_post_keepout_mm, hi_u - cfg.cutout_post_keepout_mm

    chosen: tuple[float, float, float, float] | None = None
    for idx, (cx, cy) in enumerate(candidates):
        w, h = (bu, bv) if idx < 2 else (bv, bu)
        if cx - w / 2.0 < u_lo - _EPS or cx + w / 2.0 > u_hi + _EPS:
            continue
        if cy - h / 2.0 < v_lo - _EPS or cy + h / 2.0 > v_hi + _EPS:
            continue
        if _post_hit(face, cx, cy, w, h, cfg, posts):
            continue
        clash = any(
            _rect_overlaps(cx, cy, w, h, o.cx_mm, o.cy_mm, o.width_mm, o.height_mm, m)
            for o in layout_openings
        )
        if clash:
            continue
        chosen = (cx, cy, w, h)
        break

    if chosen is None:
        issues.append(
            _issue(
                "GEOMETRY_VENT_OMITTED",
                (
                    f"No room for vent slots next to heat source "
                    f"{item.instance_id} ({item.part.name}) on the {face} "
                    "face; the design runs out of clear wall/lid area."
                ),
                severity="warning",
                part_ids=[item.instance_id],
                fix_hint=("Reduce nearby cutouts or enlarge the enclosure so the " "vents fit."),
            )
        )
        return
    cx, cy, w, h = chosen
    layout_openings.append(
        Opening(
            kind="vent",
            face=face,  # type: ignore[arg-type]
            cx_mm=cx,
            cy_mm=cy,
            width_mm=w,
            height_mm=h,
            parts=[item.instance_id],
        )
    )


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------


def pack_enclosure(
    spec: SupportedBuild,
    catalog: Catalog | None = None,
    config: EnclosureConfig | None = None,
) -> tuple[EnclosureLayout | None, list[Issue]]:
    """Deterministically lay out ``spec`` into an ``EnclosureLayout``.

    Returns ``(layout, issues)``; ``layout`` is ``None`` when an error-severity
    issue makes the design unbuildable. Warnings (e.g. omitted vents) do not
    prevent a layout.
    """
    cfg = config if config is not None else EnclosureConfig()
    cat = catalog if catalog is not None else get_catalog()
    issues: list[Issue] = []

    items = _resolve_items(spec, cat, issues)
    if any(i.severity == "error" for i in issues):
        return None, issues

    board = next((i for i in items if i.is_board), None)
    wall_items = _pack_order([i for i in items if i.mount is Mount.WALL])
    base_items = _pack_order([i for i in items if i.mount is Mount.BASE and i is not board])
    lid_items = _pack_order([i for i in items if i.mount is Mount.LID])

    for item in wall_items:
        _orient_wall_item(item, cfg)

    pinned: list[_Item] = []
    if board is not None and _pin_to_wall(board, cfg, issues):
        pinned.append(board)
    still_base: list[_Item] = []
    for item in base_items:
        if item.has_side_exposure and _pin_to_wall(item, cfg, issues):
            pinned.append(item)
        else:
            still_base.append(item)
    base_items = still_base
    if any(i.severity == "error" for i in issues):
        return None, issues

    if not _assign_walls(wall_items, cfg, issues):
        return None, issues

    # strips: the depth each wall reserves across the full inner span
    strip: dict[str, float] = {f: 0.0 for f in WALL_FACES}
    for item in pinned:
        assert item.wall is not None
        strip[item.wall] = max(strip[item.wall], item.depth + cfg.clearance_mm)
    for item in wall_items:
        assert item.wall is not None
        strip[item.wall] = max(strip[item.wall], item.depth + cfg.clearance_mm)

    keep = cfg.post_keepout_mm
    x0 = max(strip["-x"], keep)
    y0 = max(strip["-y"], keep)
    max_w = cfg.inner_max_x_mm - x0 - max(strip["+x"], keep)
    max_h = cfg.inner_max_y_mm - y0 - max(strip["+y"], keep)
    if max_w <= _EPS or max_h <= _EPS:
        issues.append(
            _issue(
                "GEOMETRY_LAYOUT_TOO_LARGE",
                (
                    f"Wall reservations and screw-post clearance leave "
                    f"{max_w:.1f} x {max_h:.1f} mm of packing area, which is "
                    f"not usable inside the {cfg.build_x_mm:.0f} x "
                    f"{cfg.build_y_mm:.0f} mm build volume."
                ),
                fix_hint="Use fewer wall-mounted parts or a larger build volume.",
            )
        )
        return None, issues

    ext_bx, ext_by = _shelf_pack(
        base_items, cfg, x0=x0, y0=y0, max_w=max_w, max_h=max_h, issues=issues
    )
    ext_lx, ext_ly = _shelf_pack(
        lid_items, cfg, x0=x0, y0=y0, max_w=max_w, max_h=max_h, issues=issues
    )
    if any(i.severity == "error" for i in issues):
        return None, issues

    def sum_eff(wall: str) -> float:
        total = 0.0
        for item in pinned + wall_items:
            if item.wall == wall:
                total += _eff_len(item, cfg)
        return total

    x_lo, x_hi = max(strip["-x"], keep), max(strip["+x"], keep)
    y_lo, y_hi = max(strip["-y"], keep), max(strip["+y"], keep)

    inner_x = max(
        x_lo + max(ext_bx, ext_lx) + x_hi,
        x_lo + max(sum_eff("-y"), sum_eff("+y")) + x_hi,
    )
    inner_y = max(
        y_lo + max(ext_by, ext_ly) + y_hi,
        y_lo + max(sum_eff("-x"), sum_eff("+x")) + y_hi,
    )
    if inner_x > cfg.inner_max_x_mm + _EPS:
        issues.append(
            _issue(
                "GEOMETRY_LAYOUT_TOO_LARGE",
                (
                    f"The layout needs {inner_x:.1f} mm of inner width but "
                    f"only {cfg.inner_max_x_mm:.1f} mm fits inside the "
                    f"{cfg.build_x_mm:.0f} mm build volume with "
                    f"{cfg.wall_mm:.1f} mm walls."
                ),
                fix_hint=(
                    "Use fewer or smaller parts, move a part to another mount "
                    "zone, or raise the build volume."
                ),
            )
        )
        return None, issues
    if inner_y > cfg.inner_max_y_mm + _EPS:
        issues.append(
            _issue(
                "GEOMETRY_LAYOUT_TOO_LARGE",
                (
                    f"The layout needs {inner_y:.1f} mm of inner depth but "
                    f"only {cfg.inner_max_y_mm:.1f} mm fits inside the "
                    f"{cfg.build_y_mm:.0f} mm build volume with "
                    f"{cfg.wall_mm:.1f} mm walls."
                ),
                fix_hint=(
                    "Use fewer or smaller parts, move a part to another mount "
                    "zone, or raise the build volume."
                ),
            )
        )
        return None, issues
    inner_x = max(inner_x, 2.0 * keep + _EPS)
    inner_y = max(inner_y, 2.0 * keep + _EPS)

    # place pinned + wall items along their walls
    by_wall: dict[str, list[_Item]] = {f: [] for f in WALL_FACES}
    for item in pinned + wall_items:
        assert item.wall is not None
        by_wall[item.wall].append(item)
    for wall, group in by_wall.items():
        if not group:
            continue
        along_lo = keep
        along_hi = (inner_y if wall in ("-x", "+x") else inner_x) - keep
        # wall-mounted items must also stay clear of the opposing strips
        if wall in ("-x", "+x"):
            along_lo = max(along_lo, y_lo)
            along_hi = min(along_hi, inner_y - y_hi)
        if wall in ("-y", "+y"):
            along_lo = max(along_lo, x_lo)
            along_hi = min(along_hi, inner_x - x_hi)
        if not _place_on_wall(group, wall, along_lo, along_hi, cfg, issues):
            return None, issues
        for item in group:
            _apply_wall_plan(item, wall, inner_x, inner_y)

    # z positions and stacks
    for item in base_items + pinned:
        item.z = cfg.standoff_height_mm if _has_holes(item) else 0.0
    for item in wall_items:
        item.z = 0.0
    floor_items = pinned + wall_items + base_items
    # pass-through prongs exit the shell; only their (clamped) head is inside
    base_top = max((i.z + i.sz for i in floor_items if not i.is_pass_through), default=0.0)
    lid_drop = max(
        (i.sz + (cfg.standoff_height_mm if _has_holes(i) else 0.0) for i in lid_items),
        default=0.0,
    )
    overlap = False
    for f in floor_items:
        fx0, fy0, fx1, fy1 = f.footprint
        for lf in lid_items:
            lx0, ly0, lx1, ly1 = lf.footprint
            if fx1 - lx0 > _EPS and lx1 - fx0 > _EPS and fy1 - ly0 > _EPS and ly1 - fy0 > _EPS:
                overlap = True
                break
        if overlap:
            break
    inner_z = (
        base_top + lid_drop + cfg.clearance_mm
        if overlap
        else max(base_top, lid_drop) + cfg.clearance_mm
    )
    if inner_z > cfg.inner_max_z_mm + _EPS:
        issues.append(
            _issue(
                "GEOMETRY_TOO_TALL",
                (
                    f"The enclosure needs {inner_z:.1f} mm of inner height "
                    f"({base_top:.1f} mm base stack"
                    f"{' + ' if overlap else ' or '}"
                    f"{lid_drop:.1f} mm lid stack + {cfg.clearance_mm:.1f} mm "
                    f"clearance) but only {cfg.inner_max_z_mm:.1f} mm fits "
                    f"inside the {cfg.build_z_mm:.0f} mm build volume."
                ),
                fix_hint=(
                    "Use shorter parts or move a tall part to another mount "
                    "zone, or raise the build volume."
                ),
            )
        )
        return None, issues
    for item in lid_items:
        boss = cfg.standoff_height_mm if _has_holes(item) else 0.0
        item.z = inner_z - boss - item.sz
    for item in wall_items:
        if item.is_pass_through and item.sz > inner_z:
            # head clamped to the cavity; the prong length sticks out below
            item.protrudes = item.sz - inner_z
            item.sz = inner_z

    # standoffs -> face holes
    packed: list[PackedPart] = []
    for item in pinned + wall_items + base_items + lid_items:
        if item.mount is Mount.WALL and item.wall is not None:
            holes = _wall_holes(item)
        else:
            holes = _base_holes(item, "floor" if item.mount is not Mount.LID else "lid")
        item.holes = holes
        packed.append(
            PackedPart(
                instance_id=item.instance_id,
                part_id=item.part.id,
                mount=item.mount,
                x_mm=item.x,
                y_mm=item.y,
                z_mm=item.z,
                size_x_mm=item.sx,
                size_y_mm=item.sy,
                size_z_mm=item.sz,
                rotation_deg=item.rotation,  # type: ignore[arg-type]
                wall=item.wall,
                heat_source=item.part.heat_source,
                protrudes_mm=item.protrudes,
                standoff_holes=holes,
            )
        )

    # openings
    openings: list[Opening] = []
    for item in pinned + wall_items + base_items + lid_items:
        opening = _item_opening(item, cfg, inner_x, inner_y, inner_z)
        if opening is not None:
            openings.append(opening)

    # posts
    inset = cfg.post_inset_mm
    posts = [
        Post(x_mm=inset, y_mm=inset, od_mm=cfg.post_od_mm, hole_mm=cfg.insert_hole_mm),
        Post(
            x_mm=inner_x - inset,
            y_mm=inset,
            od_mm=cfg.post_od_mm,
            hole_mm=cfg.insert_hole_mm,
        ),
        Post(
            x_mm=inset,
            y_mm=inner_y - inset,
            od_mm=cfg.post_od_mm,
            hole_mm=cfg.insert_hole_mm,
        ),
        Post(
            x_mm=inner_x - inset,
            y_mm=inner_y - inset,
            od_mm=cfg.post_od_mm,
            hole_mm=cfg.insert_hole_mm,
        ),
    ]

    # vents for heat sources
    for item in pinned + wall_items + base_items + lid_items:
        if item.part.heat_source:
            _vent_for(item, cfg, openings, posts, inner_x, inner_y, inner_z, issues)

    layout = EnclosureLayout(
        config=cfg,
        inner_x_mm=inner_x,
        inner_y_mm=inner_y,
        inner_z_mm=inner_z,
        base_stack_mm=base_top,
        lid_stack_mm=lid_drop,
        stacks_overlap=overlap,
        parts=packed,
        openings=openings,
        posts=posts,
    )
    return layout, issues
