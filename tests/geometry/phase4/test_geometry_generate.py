"""Phase 4 generator: STL export, report, and engine-unavailable fallback."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import sparkplan.geometry
from sparkplan.geometry.enclosure import EnclosureConfig
from sparkplan.geometry.generate import generate_enclosure
from sparkplan.models.buildspec import parse_build_spec
from sparkplan.models.catalog import Catalog, get_catalog

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _spec(name: str):
    data = json.loads((FIXTURES / name).read_text())
    return parse_build_spec(data)


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return get_catalog()


def test_generate_exports_stls_and_report(tmp_path: Path, catalog: Catalog) -> None:
    result = generate_enclosure(
        _spec("phase3/supported/soil_monitor.json"), catalog, out_dir=tmp_path
    )
    assert result.ok, [i.message for i in result.issues]
    assert result.engine == "cadquery"
    names = sorted(p.name for p in result.exported)
    assert names == ["assembled.stl", "base.stl", "lid.stl"]
    for path in result.exported:
        assert path.stat().st_size > 10_000
    assert result.report_path == tmp_path / "geometry_report.json"
    report = json.loads(result.report_path.read_text())
    assert report["ok"] is True
    assert len(report["layout"]["parts"]) == 4
    assert report["stl_files"] == ["base.stl", "lid.stl", "assembled.stl"]


def test_generate_meshes_are_watertight(catalog: Catalog) -> None:
    result = generate_enclosure(_spec("phase2/v5/valid_divider.json"), catalog)
    assert result.ok, [i.message for i in result.issues]
    assert set(result.meshes) == {"base", "lid", "assembled"}
    for name, mesh in result.meshes.items():
        tm = mesh.to_trimesh()
        assert tm.is_watertight, name
        assert tm.volume > 0.0


def test_failed_layout_writes_report_but_no_stl(tmp_path: Path, catalog: Catalog) -> None:
    tiny = EnclosureConfig(build_x_mm=50.0, build_y_mm=50.0, build_z_mm=30.0)
    result = generate_enclosure(
        _spec("phase3/supported/soil_monitor.json"),
        catalog,
        config=tiny,
        out_dir=tmp_path,
    )
    assert not result.ok
    assert result.exported == []
    assert result.meshes == {}
    assert list(tmp_path.glob("*.stl")) == []
    assert (tmp_path / "geometry_report.json").exists()
    report = json.loads((tmp_path / "geometry_report.json").read_text())
    assert report["ok"] is False
    assert any(i["severity"] == "error" for i in report["issues"])


def test_engine_unavailable_is_a_warning(
    tmp_path: Path, catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "sparkplan.geometry.shell", None)
    monkeypatch.delattr(sparkplan.geometry, "shell", raising=False)
    result = generate_enclosure(_spec("phase4/ultrasonic_alarm.json"), catalog, out_dir=tmp_path)
    codes = {i.code for i in result.issues}
    assert "GEOMETRY_ENGINE_UNAVAILABLE" in codes
    warning = next(i for i in result.issues if i.code == "GEOMETRY_ENGINE_UNAVAILABLE")
    assert warning.severity == "warning"
    # layout succeeded, so the result is still ok with warnings only
    assert result.ok
    assert result.exported == []
    assert (tmp_path / "geometry_report.json").exists()


def test_ultrasonic_alarm_generate(catalog: Catalog) -> None:
    result = generate_enclosure(_spec("phase4/ultrasonic_alarm.json"), catalog)
    assert result.ok, [i.message for i in result.issues]
    window = next(
        o for o in result.layout.openings if "range" in o.parts  # type: ignore[union-attr]
    )
    assert window.face in ("-x", "+x", "-y", "+y")
