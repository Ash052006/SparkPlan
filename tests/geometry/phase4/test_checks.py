"""Phase 4 geometry checks: hand-built layouts and synthetic meshes."""

from __future__ import annotations

import numpy as np
from sparkplan.geometry.checks import (
    check_build_volume,
    check_openings_in_face,
    check_openings_vs_posts,
    check_part_collisions,
    check_shell_thickness,
    check_solid,
    run_layout_checks,
)
from sparkplan.geometry.enclosure import (
    EnclosureConfig,
    EnclosureLayout,
    Opening,
    PackedPart,
    Post,
)
from sparkplan.geometry.engine import Mesh
from sparkplan.models.catalog import Mount


def _layout(
    *,
    inner_x: float = 60.0,
    inner_y: float = 40.0,
    inner_z: float = 15.0,
    parts: list[PackedPart] | None = None,
    openings: list[Opening] | None = None,
    posts: list[Post] | None = None,
    config: EnclosureConfig | None = None,
) -> EnclosureLayout:
    cfg = config or EnclosureConfig()
    if posts is None:
        inset = cfg.post_inset_mm
        posts = [
            Post(x_mm=inset, y_mm=inset, od_mm=cfg.post_od_mm, hole_mm=4.0),
            Post(x_mm=inner_x - inset, y_mm=inset, od_mm=cfg.post_od_mm, hole_mm=4.0),
            Post(x_mm=inset, y_mm=inner_y - inset, od_mm=cfg.post_od_mm, hole_mm=4.0),
            Post(
                x_mm=inner_x - inset,
                y_mm=inner_y - inset,
                od_mm=cfg.post_od_mm,
                hole_mm=4.0,
            ),
        ]
    return EnclosureLayout(
        config=cfg,
        inner_x_mm=inner_x,
        inner_y_mm=inner_y,
        inner_z_mm=inner_z,
        base_stack_mm=10.0,
        lid_stack_mm=0.0,
        parts=parts or [],
        openings=openings or [],
        posts=posts,
    )


def _part(
    instance_id: str,
    x: float,
    y: float,
    z: float,
    sx: float = 10.0,
    sy: float = 10.0,
    sz: float = 5.0,
) -> PackedPart:
    return PackedPart(
        instance_id=instance_id,
        part_id="some_part",
        mount=Mount.BASE,
        x_mm=x,
        y_mm=y,
        z_mm=z,
        size_x_mm=sx,
        size_y_mm=sy,
        size_z_mm=sz,
    )


def test_colliding_parts_flagged() -> None:
    layout = _layout(parts=[_part("a", 0, 0, 0), _part("b", 5, 5, 0)])
    issues = check_part_collisions(layout)
    assert len(issues) == 1
    assert issues[0].code == "GEOMETRY_PART_COLLISION"
    assert issues[0].part_ids == ["a", "b"]


def test_separated_parts_pass() -> None:
    layout = _layout(parts=[_part("a", 0, 0, 0), _part("b", 30, 0, 0)])
    assert check_part_collisions(layout) == []


def test_clearance_below_minimum_flagged() -> None:
    # overlap-free but only 0.5 mm apart (default clearance is 1.0 mm)
    layout = _layout(parts=[_part("a", 0, 0, 0, sx=10), _part("b", 10.5, 0, 0)])
    issues = check_part_collisions(layout)
    assert any(i.code == "GEOMETRY_PART_COLLISION" for i in issues)


def test_cutout_off_face_flagged() -> None:
    layout = _layout(
        openings=[
            Opening(face="-x", cx_mm=0.0, cy_mm=7.5, width_mm=10.0, height_mm=5.0),
        ]
    )
    issues = check_openings_in_face(layout)
    assert len(issues) == 1
    assert issues[0].code == "GEOMETRY_CUTOUT_NOT_IN_FACE"


def test_cutout_inside_face_passes() -> None:
    layout = _layout(
        openings=[
            Opening(face="-x", cx_mm=20.0, cy_mm=7.5, width_mm=10.0, height_mm=5.0),
            Opening(face="lid", cx_mm=30.0, cy_mm=20.0, width_mm=12.0, height_mm=8.0),
        ]
    )
    assert check_openings_in_face(layout) == []


def test_cutout_overlapping_post_flagged() -> None:
    layout = _layout(
        openings=[
            # floor cutout centred on the corner post at (3, 3)
            Opening(face="floor", cx_mm=3.0, cy_mm=3.0, width_mm=8.0, height_mm=8.0),
        ]
    )
    issues = check_openings_vs_posts(layout)
    assert len(issues) == 1
    assert issues[0].code == "GEOMETRY_CUTOUT_POST_CLASH"


def test_cutout_clear_of_posts_passes() -> None:
    layout = _layout(
        openings=[
            Opening(face="floor", cx_mm=30.0, cy_mm=20.0, width_mm=12.0, height_mm=8.0),
        ]
    )
    assert check_openings_vs_posts(layout) == []


def test_build_volume_exceeded() -> None:
    cfg = EnclosureConfig(build_x_mm=40.0)
    layout = _layout(inner_x=60.0, config=cfg)
    issues = check_build_volume(layout)
    assert any(i.code == "GEOMETRY_BUILD_VOLUME" for i in issues)


def test_shell_thickness_below_minimum() -> None:
    cfg = EnclosureConfig(min_wall_mm=3.0)  # walls default to 2.4 mm
    issues = check_shell_thickness(cfg)
    assert len(issues) == 3  # wall, floor and lid are all 2.4 < 3.0
    assert all(i.code == "GEOMETRY_WALL_THIN" for i in issues)


def test_run_layout_checks_clean() -> None:
    layout = _layout(parts=[_part("a", 20, 15, 0)])
    assert run_layout_checks(layout) == []


def test_open_box_mesh_is_not_watertight() -> None:
    verts = np.array(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0]],
        dtype=np.float64,
    )
    faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64)
    issues = check_solid("base", Mesh(vertices=verts, faces=faces))
    codes = {i.code for i in issues}
    assert "GEOMETRY_NOT_WATERTIGHT" in codes
    assert "GEOMETRY_NONPOSITIVE_VOLUME" in codes
    assert all(i.severity == "error" for i in issues)


def test_valid_cube_mesh_passes() -> None:
    import trimesh

    box = trimesh.creation.box(extents=(10.0, 10.0, 10.0))
    mesh = Mesh(
        vertices=np.asarray(box.vertices, dtype=np.float64),
        faces=np.asarray(box.faces, dtype=np.int64),
    )
    assert check_solid("lid", mesh) == []
