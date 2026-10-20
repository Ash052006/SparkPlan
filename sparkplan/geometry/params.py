"""Deterministic enclosure parameters derived from catalog dimensions.

The LLM has no say here: sizes come only from catalog mechanical data and
the validated BuildSpec.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from sparkplan.catalog import Catalog
from sparkplan.models.spec import BuildSpec

WALL_MM = 2.0
FLOOR_MM = 2.0
LID_MM = 2.0
CLEARANCE_MM = 2.0
STANDOFF_MM = 3.0
HEADROOM_MM = 6.0
CABLE_NOTCH_WIDTH_MM = 10.0
CABLE_NOTCH_DEPTH_MM = 6.0
BOSS_OD_MM = 6.0
BOSS_HOLE_MM = 2.7
LID_LIP_MM = 1.2


class Placement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: str
    part_id: str
    x_mm: float
    y_mm: float
    footprint_x_mm: float
    footprint_y_mm: float
    height_mm: float


class Cutout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str  # usb | cable_notch
    wall: str  # -x | +x | -y | +y
    center_mm: float  # position along the wall
    width_mm: float
    height_mm: float
    bottom_mm: float = 0.0


class EnclosureParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inner_x_mm: float
    inner_y_mm: float
    inner_z_mm: float
    wall_mm: float = WALL_MM
    floor_mm: float = FLOOR_MM
    lid_mm: float = LID_MM
    standoff_mm: float = STANDOFF_MM
    boss_od_mm: float = BOSS_OD_MM
    boss_hole_mm: float = BOSS_HOLE_MM
    lid_lip_mm: float = LID_LIP_MM
    placements: list[Placement] = Field(default_factory=list)
    cutouts: list[Cutout] = Field(default_factory=list)

    @property
    def outer_x_mm(self) -> float:
        return self.inner_x_mm + 2 * self.wall_mm

    @property
    def outer_y_mm(self) -> float:
        return self.inner_y_mm + 2 * self.wall_mm

    @property
    def base_height_mm(self) -> float:
        return self.floor_mm + self.inner_z_mm

    @property
    def total_height_mm(self) -> float:
        return self.base_height_mm + self.lid_mm


class EnclosurePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    params: EnclosureParams
    controller_id: str


def compute_enclosure_plan(spec: BuildSpec, catalog: Catalog) -> EnclosurePlan:
    """Shelf-pack parts on the floor; derive inner dimensions and cutouts."""
    controller = catalog.controller
    placements: list[Placement] = []

    # Controller along the -X wall, USB facing -X.
    c = controller.mechanical
    placements.append(
        Placement(
            instance_id="controller",
            part_id=controller.part_id,
            x_mm=CLEARANCE_MM,
            y_mm=CLEARANCE_MM,
            footprint_x_mm=c.length_mm,
            footprint_y_mm=c.width_mm,
            height_mm=c.height_mm,
        )
    )

    cursor_x = CLEARANCE_MM + c.length_mm + CLEARANCE_MM
    cursor_y = CLEARANCE_MM
    row_depth = c.width_mm
    max_x = cursor_x

    for inst in spec.parts:
        part = catalog.part_or_none(inst.part_id)
        if part is None or part.category.value == "power":
            continue  # battery/boost sit anywhere; keep enclosure sized for boards
        m = part.mechanical
        fx, fy = m.length_mm, m.width_mm
        if fx < fy:  # orient the long axis along +Y within its cell
            fx, fy = fy, fx
        if cursor_y + fy + CLEARANCE_MM > row_depth + CLEARANCE_MM * 2:
            cursor_x = max_x + CLEARANCE_MM
            cursor_y = CLEARANCE_MM
        placements.append(
            Placement(
                instance_id=inst.instance_id,
                part_id=part.part_id,
                x_mm=cursor_x,
                y_mm=cursor_y,
                footprint_x_mm=fx,
                footprint_y_mm=fy,
                height_mm=m.height_mm,
            )
        )
        cursor_y += fy + CLEARANCE_MM
        max_x = max(max_x, cursor_x + fx)

    inner_x = max_x + CLEARANCE_MM
    inner_y = max(
        row_depth + 2 * CLEARANCE_MM,
        max(p.y_mm + p.footprint_y_mm for p in placements) + CLEARANCE_MM,
    )

    tallest = max(p.height_mm for p in placements)
    inner_z = STANDOFF_MM + tallest + HEADROOM_MM

    cutouts: list[Cutout] = []
    usb = c.usb_connector
    if usb is not None:
        cutouts.append(
            Cutout(
                kind="usb",
                wall="-x",
                center_mm=CLEARANCE_MM + usb.offset_mm,
                width_mm=usb.width_mm + 1.0,
                height_mm=usb.height_mm + 1.5,
                bottom_mm=FLOOR_MM + STANDOFF_MM - 0.5,
            )
        )
    for p in placements[1:]:
        cutouts.append(
            Cutout(
                kind="cable_notch",
                wall="+x",
                center_mm=p.y_mm + p.footprint_y_mm / 2.0,
                width_mm=min(CABLE_NOTCH_WIDTH_MM, p.footprint_y_mm),
                height_mm=CABLE_NOTCH_DEPTH_MM,
                bottom_mm=FLOOR_MM + inner_z - CABLE_NOTCH_DEPTH_MM,
            )
        )

    params = EnclosureParams(
        inner_x_mm=round(inner_x, 2),
        inner_y_mm=round(inner_y, 2),
        inner_z_mm=round(inner_z, 2),
        placements=placements,
        cutouts=cutouts,
    )
    return EnclosurePlan(params=params, controller_id="controller")
