# Phase 4 stack first: generate imports shell (CadQuery) lazily, so
# importing this package never requires a solid-modelling engine.
from sparkplan.geometry.checks import run_layout_checks, run_mesh_checks
from sparkplan.geometry.enclosure import (
    EnclosureConfig,
    EnclosureLayout,
    Opening,
    PackedPart,
    Post,
)
from sparkplan.geometry.engine import (
    EnclosureMeshes,
    GeometryEngineError,
    Mesh,
    available_engines,
    build_enclosure,
)
from sparkplan.geometry.generate import GeometryResult, generate_enclosure
from sparkplan.geometry.layout import pack_enclosure
from sparkplan.geometry.params import (
    Cutout,
    EnclosureParams,
    EnclosurePlan,
    Placement,
    compute_enclosure_plan,
)

__all__ = [
    "Cutout",
    "EnclosureConfig",
    "EnclosureLayout",
    "EnclosureMeshes",
    "EnclosureParams",
    "EnclosurePlan",
    "GeometryEngineError",
    "GeometryResult",
    "Mesh",
    "Opening",
    "PackedPart",
    "Placement",
    "Post",
    "available_engines",
    "build_enclosure",
    "compute_enclosure_plan",
    "generate_enclosure",
    "pack_enclosure",
    "run_layout_checks",
    "run_mesh_checks",
]
