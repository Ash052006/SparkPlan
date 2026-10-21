"""Phase 4 enclosure generator: layout -> checks -> solids -> STL + report.

``generate_enclosure`` is the single entry point. It never raises for design
problems: everything comes back as ``Issue`` objects on ``GeometryResult``.
CadQuery is imported lazily so an environment without a solid-modelling
engine degrades to a ``GEOMETRY_ENGINE_UNAVAILABLE`` warning (the planner
then skips geometry feedback instead of failing the run).

STL export happens only when no error-severity issue exists;
``geometry_report.json`` is written whenever ``out_dir`` is given.

No LLM, no network beyond the local filesystem. Units: millimetres.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from sparkplan.geometry.checks import has_errors, run_layout_checks, run_mesh_checks
from sparkplan.geometry.enclosure import EnclosureConfig, EnclosureLayout
from sparkplan.geometry.engine import Mesh
from sparkplan.geometry.layout import pack_enclosure
from sparkplan.models.buildspec import SupportedBuild
from sparkplan.models.catalog import Catalog, get_catalog
from sparkplan.models.spec import Issue

_STL_NAMES = ("base", "lid", "assembled")
_REPORT_NAME = "geometry_report.json"


def _issue(
    code: str,
    message: str,
    *,
    severity: str = "warning",
    fix_hint: str = "",
) -> Issue:
    return Issue(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        message=message,
        part_ids=[],
        fix_hint=fix_hint,
    )


@dataclass
class GeometryResult:
    """Everything the pipeline needs to know about the enclosure attempt."""

    layout: EnclosureLayout | None
    issues: list[Issue] = field(default_factory=list)
    meshes: dict[str, Mesh] = field(default_factory=dict)
    engine: str | None = None
    exported: list[Path] = field(default_factory=list)
    report_path: Path | None = None

    @property
    def ok(self) -> bool:
        """True when the enclosure is buildable (no error issues)."""
        return self.layout is not None and not has_errors(self.issues)

    @property
    def error_issues(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]


def generate_enclosure(
    spec: SupportedBuild,
    catalog: Catalog | None = None,
    config: EnclosureConfig | None = None,
    out_dir: str | Path | None = None,
) -> GeometryResult:
    """Deterministically build the enclosure for ``spec``.

    Returns a ``GeometryResult``; check ``ok`` / ``issues`` before trusting
    ``meshes`` or ``exported``.
    """
    cat = catalog if catalog is not None else get_catalog()
    layout, issues = pack_enclosure(spec, cat, config)
    result = GeometryResult(layout=layout, issues=list(issues))
    if layout is None or has_errors(result.issues):
        if out_dir is not None:
            _write_report(result, Path(out_dir))
        return result

    result.issues.extend(run_layout_checks(layout))

    try:
        from sparkplan.geometry import shell
    except ImportError as exc:  # no CadQuery/build stack in this environment
        result.issues.append(
            _issue(
                "GEOMETRY_ENGINE_UNAVAILABLE",
                f"Solid-modelling engine unavailable ({exc}); the layout "
                "was computed but no STL could be built.",
                severity="warning",
                fix_hint="Install cadquery in the runtime environment.",
            )
        )
        if out_dir is not None:
            _write_report(result, Path(out_dir))
        return result

    result.engine = "cadquery"
    try:
        built = shell.meshes(layout)
    except Exception as exc:  # boolean/geometry failure on a valid layout
        result.issues.append(
            _issue(
                "GEOMETRY_BUILD_FAILED",
                f"Building the enclosure solids failed: {exc}",
                severity="error",
                fix_hint="Report the design; the layout numbers were valid.",
            )
        )
        if out_dir is not None:
            _write_report(result, Path(out_dir))
        return result

    result.meshes = dict(zip(_STL_NAMES, built, strict=True))
    result.issues.extend(run_mesh_checks(result.meshes))

    if out_dir is not None:
        out = Path(out_dir)
        if not has_errors(result.issues):
            result.exported = _export_stls(result, out)
        _write_report(result, out)
    return result


def _export_stls(result: GeometryResult, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for name in _STL_NAMES:
        path = out_dir / f"{name}.stl"
        result.meshes[name].to_trimesh().export(path)
        paths.append(path)
    return paths


def _write_report(result: GeometryResult, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "ok": result.ok,
        "engine": result.engine,
        "issues": [i.model_dump() for i in result.issues],
        "stl_files": [p.name for p in result.exported],
        "layout": None,
    }
    if result.layout is not None:
        lay = result.layout
        report["layout"] = {
            "inner_mm": [lay.inner_x_mm, lay.inner_y_mm, lay.inner_z_mm],
            "outer_mm": [lay.outer_x_mm, lay.outer_y_mm, lay.outer_z_mm],
            "base_stack_mm": lay.base_stack_mm,
            "lid_stack_mm": lay.lid_stack_mm,
            "stacks_overlap": lay.stacks_overlap,
            "parts": [p.model_dump(mode="json") for p in lay.parts],
            "openings": [o.model_dump(mode="json") for o in lay.openings],
            "posts": [p.model_dump(mode="json") for p in lay.posts],
            "config": lay.config.model_dump(mode="json"),
        }
    path = out_dir / _REPORT_NAME
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    result.report_path = path
