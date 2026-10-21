"""Planner <-> enclosure geometry feedback. Offline: FakeLLM only."""

from __future__ import annotations

from sparkplan.geometry.generate import GeometryResult
from sparkplan.llm.phase3 import FakeLLM
from sparkplan.models.spec import Issue
from sparkplan.planner.phase3 import planner as planner_mod
from sparkplan.planner.phase3.models import PlanStatus
from sparkplan.planner.phase3.planner import Planner
from sparkplan.validators.phase2.report import run_all
from tests.planner.phase3.conftest import as_supported, valid_spec

_GEOM_ERROR = Issue(
    code="GEOMETRY_PART_COLLISION",
    severity="error",
    message="board and screen collide.",
    part_ids=["board", "screen"],
    fix_hint="Move one part to another mount zone.",
)

_GEOM_WARNING = Issue(
    code="GEOMETRY_VENT_OMITTED",
    severity="warning",
    message="No room for vents.",
)


def test_geometry_error_gets_one_repair_then_ok(monkeypatch, catalog) -> None:
    spec = as_supported(valid_spec())
    assert run_all(spec, catalog).passed

    results = [
        GeometryResult(layout=None, issues=[_GEOM_ERROR]),
        GeometryResult(layout=None, issues=[_GEOM_WARNING]),
    ]

    def fake_generate(spec_arg, catalog_arg=None, **kwargs):
        return results.pop(0)

    monkeypatch.setattr(planner_mod, "generate_enclosure", fake_generate)
    planner = Planner(FakeLLM(script=[valid_spec(), valid_spec()]), catalog)
    result = planner.plan("a soil moisture monitor")

    assert result.status is PlanStatus.OK
    kinds = [a.kind for a in result.attempts]
    assert kinds == ["geometry_error", "ok"]
    assert any(i.code == "GEOMETRY_VENT_OMITTED" for i in result.issues)
    assert result.llm_calls == 2


def test_geometry_error_twice_is_terminal(monkeypatch, catalog) -> None:
    spec = as_supported(valid_spec())
    assert run_all(spec, catalog).passed

    def fake_generate(spec_arg, catalog_arg=None, **kwargs):
        return GeometryResult(layout=None, issues=[_GEOM_ERROR])

    monkeypatch.setattr(planner_mod, "generate_enclosure", fake_generate)
    planner = Planner(FakeLLM(script=[valid_spec(), valid_spec()]), catalog)
    result = planner.plan("a soil moisture monitor")

    assert result.status is PlanStatus.FAILED
    kinds = [a.kind for a in result.attempts]
    assert kinds == ["geometry_error", "geometry_error"]
    assert any(i.code == "GEOMETRY_PART_COLLISION" for i in result.issues)
    assert not result.ok


def test_geometry_warning_alone_keeps_ok(monkeypatch, catalog) -> None:
    def fake_generate(spec_arg, catalog_arg=None, **kwargs):
        return GeometryResult(layout=None, issues=[_GEOM_WARNING])

    monkeypatch.setattr(planner_mod, "generate_enclosure", fake_generate)
    planner = Planner(FakeLLM(script=[valid_spec()]), catalog)
    result = planner.plan("a soil moisture monitor")

    assert result.status is PlanStatus.OK
    assert any(i.code == "GEOMETRY_VENT_OMITTED" for i in result.issues)
    assert result.attempts[-1].kind == "ok"
