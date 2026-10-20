"""Shared helpers for Phase 2 validator tests."""

from __future__ import annotations

import json
from pathlib import Path

from sparkplan.models.buildspec import SupportedBuild, parse_build_spec
from sparkplan.validators.phase2 import ValidationReport, run_all

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "phase2"


def load(name: str) -> SupportedBuild:
    """Load a fixture by relative path, e.g. 'v1/unknown_part.json'."""
    data = json.loads((FIXTURES / name).read_text())
    spec = parse_build_spec(data)
    assert isinstance(spec, SupportedBuild)
    return spec


def run(name: str) -> ValidationReport:
    return run_all(load(name))


def codes(report: ValidationReport) -> set[str]:
    return {i.code for i in report.issues}


def error_codes(report: ValidationReport) -> set[str]:
    return {i.code for i in report.errors}
