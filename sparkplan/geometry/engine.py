"""Geometry engine selection: CadQuery, else build123d.

Both engines consume the same EnclosurePlan (pure numbers from catalog
dimensions) and produce identical-structure meshes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh

from sparkplan.geometry.params import EnclosureParams, EnclosurePlan


class GeometryEngineError(RuntimeError):
    """No usable solid-modelling engine, or mesh construction failed."""


@dataclass(frozen=True)
class Mesh:
    vertices: np.ndarray  # (N, 3) float64
    faces: np.ndarray  # (M, 3) int64

    def to_trimesh(self) -> trimesh.Trimesh:
        mesh = trimesh.Trimesh(vertices=self.vertices, faces=self.faces, process=True)
        mesh.merge_vertices()
        return mesh


@dataclass(frozen=True)
class EnclosureMeshes:
    base: Mesh
    lid: Mesh
    params: EnclosureParams

    def base_trimesh(self) -> trimesh.Trimesh:
        return self.base.to_trimesh()

    def lid_trimesh(self) -> trimesh.Trimesh:
        return self.lid.to_trimesh()

    def export_base_stl(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.base_trimesh().export(path)
        return path

    def export_lid_stl(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lid_trimesh().export(path)
        return path


def available_engines() -> list[str]:
    engines: list[str] = []
    try:
        import cadquery  # noqa: F401

        engines.append("cadquery")
    except Exception:  # noqa: BLE001
        pass
    try:
        import build123d  # noqa: F401

        engines.append("build123d")
    except Exception:  # noqa: BLE001
        pass
    return engines


def build_enclosure(plan: EnclosurePlan) -> EnclosureMeshes:
    engines = available_engines()
    if "cadquery" in engines:
        from sparkplan.geometry.cadquery_engine import build as build_with

        return build_with(plan)
    if "build123d" in engines:
        from sparkplan.geometry.build123d_engine import build as build_with

        return build_with(plan)
    raise GeometryEngineError(
        "no geometry engine available: install cadquery (preferred) or build123d"
    )
