"""Deterministic geometry checks for Phase 4.

Every check returns ``Issue`` objects (code, severity, message, part_ids,
fix_hint) instead of raising, so problems can be fed back to the planner.
Two families:

* **layout checks** - pure numbers from an ``EnclosureLayout``: build volume,
  part collisions, cutouts staying on their face, cutouts clearing the screw
  posts, shell wall thickness.
* **mesh checks** - on tessellated solids: watertightness and positive volume.

No LLM, no network, no I/O. Units: millimetres.
"""

from __future__ import annotations

from sparkplan.geometry.enclosure import EnclosureConfig, EnclosureLayout
from sparkplan.geometry.engine import Mesh
from sparkplan.models.spec import Issue

_EPS = 1e-6

_WALL_FACES = ("-x", "+x", "-y", "+y")


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


# ---------------------------------------------------------------------------
# mesh checks
# ---------------------------------------------------------------------------


def check_solid(name: str, mesh: Mesh) -> list[Issue]:
    """Watertightness and positive volume for one tessellated solid."""
    issues: list[Issue] = []
    tm = mesh.to_trimesh()
    if not tm.is_watertight:
        issues.append(
            _issue(
                "GEOMETRY_NOT_WATERTIGHT",
                f"The {name} solid is not watertight, so its STL would not " "slice reliably.",
                fix_hint=(
                    "Re-run generation; if it persists, simplify the design "
                    "or report the fixture that triggered it."
                ),
            )
        )
    if tm.volume <= _EPS:
        issues.append(
            _issue(
                "GEOMETRY_NONPOSITIVE_VOLUME",
                f"The {name} solid has volume {tm.volume:.3f} mm^3, which "
                "means the boolean construction produced an empty or "
                "inverted body.",
                fix_hint="Re-run generation; the enclosure shape is degenerate.",
            )
        )
    return issues


def run_mesh_checks(named_meshes: dict[str, Mesh]) -> list[Issue]:
    issues: list[Issue] = []
    for name, mesh in named_meshes.items():
        issues.extend(check_solid(name, mesh))
    return issues


# ---------------------------------------------------------------------------
# layout checks
# ---------------------------------------------------------------------------


def check_build_volume(layout: EnclosureLayout) -> list[Issue]:
    cfg = layout.config
    issues: list[Issue] = []
    for axis, outer, build in (
        ("X", layout.outer_x_mm, cfg.build_x_mm),
        ("Y", layout.outer_y_mm, cfg.build_y_mm),
        ("Z", layout.outer_z_mm, cfg.build_z_mm),
    ):
        if outer > build + _EPS:
            issues.append(
                _issue(
                    "GEOMETRY_BUILD_VOLUME",
                    f"The enclosure is {outer:.1f} mm along {axis} but the "
                    f"build volume is only {build:.1f} mm.",
                    fix_hint=(
                        "Use fewer or smaller parts so the shell shrinks, or "
                        "raise the build volume in EnclosureConfig."
                    ),
                )
            )
    return issues


def check_shell_thickness(config: EnclosureConfig) -> list[Issue]:
    issues: list[Issue] = []
    for name, value in (
        ("wall", config.wall_mm),
        ("floor", config.floor_mm),
        ("lid", config.lid_mm),
    ):
        if value < config.min_wall_mm - _EPS:
            issues.append(
                _issue(
                    "GEOMETRY_WALL_THIN",
                    f"The {name} thickness {value:.1f} mm is below the "
                    f"minimum printable {config.min_wall_mm:.1f} mm.",
                    fix_hint=f"Raise {name}_mm to at least {config.min_wall_mm}.",
                )
            )
    return issues


def _gap(a0: float, a1: float, b0: float, b1: float) -> float:
    """Signed clearance between two intervals (negative = overlap depth)."""
    if a1 < b0:
        return b0 - a1
    if b1 < a0:
        return a0 - b1
    return -(min(a1, b1) - max(a0, b0))


def check_part_collisions(layout: EnclosureLayout) -> list[Issue]:
    """Flag part AABBs that overlap or sit closer than the clearance."""
    clr = layout.config.clearance_mm
    issues: list[Issue] = []
    parts = layout.parts
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            a, b = parts[i], parts[j]
            gx = _gap(a.x_mm, a.x_mm + a.size_x_mm, b.x_mm, b.x_mm + b.size_x_mm)
            gy = _gap(a.y_mm, a.y_mm + a.size_y_mm, b.y_mm, b.y_mm + b.size_y_mm)
            gz = _gap(a.z_mm, a.z_mm + a.size_z_mm, b.z_mm, b.z_mm + b.size_z_mm)
            if gx < clr - _EPS and gy < clr - _EPS and gz < clr - _EPS:
                issues.append(
                    _issue(
                        "GEOMETRY_PART_COLLISION",
                        f"{a.instance_id} and {b.instance_id} collide or "
                        f"touch: gaps are {max(gx, 0.0):.1f} / "
                        f"{max(gy, 0.0):.1f} / {max(gz, 0.0):.1f} mm on x/y/z "
                        f"but {clr:.1f} mm clearance is required.",
                        part_ids=[a.instance_id, b.instance_id],
                        fix_hint=(
                            "Move one part to another mount zone, drop a "
                            "part, or raise clearance-aware packing by "
                            "enlarging the build volume."
                        ),
                    )
                )
    return issues


def check_openings_in_face(layout: EnclosureLayout) -> list[Issue]:
    """Every cutout must lie inside the face it is cut into (inclusive)."""
    issues: list[Issue] = []
    for o in layout.openings:
        if o.face in _WALL_FACES:
            length = layout.inner_y_mm if o.face in ("-x", "+x") else layout.inner_x_mm
            lo_u, hi_u, lo_v, hi_v = 0.0, length, 0.0, layout.inner_z_mm
            u0, u1 = o.cx_mm - o.width_mm / 2.0, o.cx_mm + o.width_mm / 2.0
            v0, v1 = o.cy_mm - o.height_mm / 2.0, o.cy_mm + o.height_mm / 2.0
            bad = u0 < lo_u - _EPS or u1 > hi_u + _EPS or v0 < lo_v - _EPS or v1 > hi_v + _EPS
            where = f"the {o.face} wall (0..{hi_u:.1f} x 0..{hi_v:.1f})"
        else:
            lo_u, hi_u = 0.0, layout.inner_x_mm
            lo_v, hi_v = 0.0, layout.inner_y_mm
            u0, u1 = o.cx_mm - o.width_mm / 2.0, o.cx_mm + o.width_mm / 2.0
            v0, v1 = o.cy_mm - o.height_mm / 2.0, o.cy_mm + o.height_mm / 2.0
            bad = u0 < lo_u - _EPS or u1 > hi_u + _EPS or v0 < lo_v - _EPS or v1 > hi_v + _EPS
            where = f"the {o.face} (0..{hi_u:.1f} x 0..{hi_v:.1f})"
        if bad:
            issues.append(
                _issue(
                    "GEOMETRY_CUTOUT_NOT_IN_FACE",
                    f"The cutout for {', '.join(o.parts) or o.kind} at "
                    f"({o.cx_mm:.1f}, {o.cy_mm:.1f}) with size "
                    f"{o.width_mm:.1f} x {o.height_mm:.1f} mm falls off "
                    f"{where}.",
                    part_ids=list(o.parts),
                    fix_hint=(
                        "Move the part away from the enclosure edge or " "enlarge the enclosure."
                    ),
                )
            )
    return issues


def check_openings_vs_posts(layout: EnclosureLayout) -> list[Issue]:
    """Cutouts must clear the four corner screw posts."""
    issues: list[Issue] = []
    for o in layout.openings:
        for post in layout.posts:
            r = post.od_mm / 2.0
            if o.face in _WALL_FACES:
                # post runs the full inner height; only along-wall distance
                along_post = post.y_mm if o.face in ("-x", "+x") else post.x_mm
                clash = abs(o.cx_mm - along_post) < o.width_mm / 2.0 + r - _EPS
                # for wall faces width is the along-wall extent
            else:
                clash = (
                    abs(o.cx_mm - post.x_mm) < o.width_mm / 2.0 + r - _EPS
                    and abs(o.cy_mm - post.y_mm) < o.height_mm / 2.0 + r - _EPS
                )
            if clash:
                issues.append(
                    _issue(
                        "GEOMETRY_CUTOUT_POST_CLASH",
                        f"The cutout for {', '.join(o.parts) or o.kind} on "
                        f"{o.face} overlaps corner screw post at "
                        f"({post.x_mm:.1f}, {post.y_mm:.1f}) "
                        f"(post diameter {post.od_mm:.1f} mm).",
                        part_ids=list(o.parts),
                        fix_hint=(
                            "Move the part away from the enclosure corner so "
                            "its cutout clears the screw posts."
                        ),
                    )
                )
                break  # one report per opening is enough
    return issues


def run_layout_checks(layout: EnclosureLayout) -> list[Issue]:
    """All number-only checks, in a stable order."""
    issues: list[Issue] = []
    issues.extend(check_shell_thickness(layout.config))
    issues.extend(check_build_volume(layout))
    issues.extend(check_part_collisions(layout))
    issues.extend(check_openings_in_face(layout))
    issues.extend(check_openings_vs_posts(layout))
    return issues


def has_errors(issues: list[Issue]) -> bool:
    return any(i.severity == "error" for i in issues)
