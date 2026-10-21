"""Phase 4 enclosure models: configuration and deterministic layout results.

Everything here is geometry-only: no LLM, no I/O. Units are millimetres.
The legacy enclosure models in ``sparkplan.geometry.params`` stay untouched;
this module drives the new-stack ``SupportedBuild`` + Phase 1 catalog.

Coordinate frames
-----------------

* **inner frame** - ``x``/``y`` span the inner cavity (0..inner_x, 0..inner_y);
  ``z`` is the height above the inner floor (0 = floor top).
* **face frame** (``u``, ``v``) - for a wall face, ``u`` runs along the wall
  and ``v`` is the height above the inner floor; for ``lid``/``floor`` faces
  ``u``/``v`` are the plan ``x``/``y``.
* **outer frame** - the shell's outer min corner is the origin; inner frame
  maps to it by ``+wall`` in x/y and ``+floor`` in z.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sparkplan.models.catalog import Mount

Face = Literal["-x", "+x", "-y", "+y", "lid", "floor"]

WALL_FACES: tuple[str, ...] = ("-x", "+x", "-y", "+y")
WALL_FACE_ORDER: tuple[str, ...] = ("-y", "+y", "+x", "-x")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EnclosureConfig(_Strict):
    """All tunable enclosure parameters (millimetres unless noted)."""

    wall_mm: float = Field(default=2.4, gt=0)
    floor_mm: float = Field(default=2.4, gt=0)
    lid_mm: float = Field(default=2.4, gt=0)
    clearance_mm: float = Field(default=1.0, ge=0)
    standoff_height_mm: float = Field(default=4.0, gt=0)
    corner_radius_mm: float = Field(default=3.0, ge=0)
    insert_hole_mm: float = Field(default=4.0, gt=0)
    post_od_mm: float = Field(default=8.0, gt=0)
    lid_lip_mm: float = Field(default=1.5, gt=0)
    lid_lip_gap_mm: float = Field(default=0.25, ge=0)
    lid_screw_hole_mm: float = Field(default=3.2, gt=0)
    cutout_tolerance_mm: float = Field(default=0.5, ge=0)
    min_wall_mm: float = Field(default=1.6, gt=0)
    build_x_mm: float = Field(default=220.0, gt=0)
    build_y_mm: float = Field(default=220.0, gt=0)
    build_z_mm: float = Field(default=250.0, gt=0)
    tessellation_mm: float = Field(default=0.05, gt=0)
    insert_depth_mm: float = Field(default=6.0, gt=0)
    vent_count: int = Field(default=4, ge=1)
    vent_slot_mm: float = Field(default=2.4, gt=0)
    vent_slot_length_mm: float = Field(default=15.0, gt=0)
    vent_gap_mm: float = Field(default=3.0, gt=0)
    vent_margin_mm: float = Field(default=3.0, ge=0)

    @property
    def post_inset_mm(self) -> float:
        """Distance from each inner wall to a screw-post centre.

        Slightly less than ``post_od/2`` so the post overlaps the wall and
        unions into it without a tangency artefact.
        """
        return self.post_od_mm / 2.0 - 1.0

    @property
    def post_keepout_mm(self) -> float:
        """Along-wall distance from a wall end that part bodies must clear."""
        return self.post_inset_mm + self.post_od_mm / 2.0 + self.clearance_mm

    @property
    def cutout_post_keepout_mm(self) -> float:
        """Along-wall distance cutout edges must keep from post centres."""
        return self.post_inset_mm + self.post_od_mm / 2.0

    @property
    def inner_max_x_mm(self) -> float:
        return self.build_x_mm - 2.0 * self.wall_mm

    @property
    def inner_max_y_mm(self) -> float:
        return self.build_y_mm - 2.0 * self.wall_mm

    @property
    def inner_max_z_mm(self) -> float:
        return self.build_z_mm - self.floor_mm - self.lid_mm


class FaceHole(_Strict):
    """A part mounting hole mapped to a shell face (for standoffs)."""

    face: Face
    u_mm: float
    v_mm: float
    diameter: float = Field(gt=0)


class PackedPart(_Strict):
    """One part instance placed in the inner frame."""

    instance_id: str
    part_id: str
    mount: Mount
    x_mm: float
    y_mm: float
    z_mm: float
    size_x_mm: float = Field(gt=0)
    size_y_mm: float = Field(gt=0)
    size_z_mm: float = Field(gt=0)
    rotation_deg: Literal[0, 90, 180, 270] = 0
    wall: str | None = None
    heat_source: bool = False
    protrudes_mm: float = Field(default=0.0, ge=0)
    standoff_holes: list[FaceHole] = Field(default_factory=list)


class Opening(_Strict):
    """A rectangular or circular cutout in one shell face."""

    kind: Literal["exposure", "vent"] = "exposure"
    shape: Literal["rect", "circle"] = "rect"
    face: Face
    cx_mm: float
    cy_mm: float
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    parts: list[str] = Field(default_factory=list)


class Post(_Strict):
    """One of the four corner screw posts (insert hole for the lid screw)."""

    x_mm: float
    y_mm: float
    od_mm: float = Field(gt=0)
    hole_mm: float = Field(gt=0)


class EnclosureLayout(_Strict):
    """The complete deterministic plan for one enclosure."""

    config: EnclosureConfig
    inner_x_mm: float = Field(gt=0)
    inner_y_mm: float = Field(gt=0)
    inner_z_mm: float = Field(gt=0)
    base_stack_mm: float = Field(ge=0)
    lid_stack_mm: float = Field(ge=0)
    stacks_overlap: bool = False
    parts: list[PackedPart] = Field(default_factory=list)
    openings: list[Opening] = Field(default_factory=list)
    posts: list[Post] = Field(default_factory=list)

    @property
    def outer_x_mm(self) -> float:
        return self.inner_x_mm + 2.0 * self.config.wall_mm

    @property
    def outer_y_mm(self) -> float:
        return self.inner_y_mm + 2.0 * self.config.wall_mm

    @property
    def outer_z_mm(self) -> float:
        return self.config.floor_mm + self.inner_z_mm + self.config.lid_mm

    def exposure_openings(self) -> list[Opening]:
        return [o for o in self.openings if o.kind == "exposure"]
