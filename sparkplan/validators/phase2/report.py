"""Phase 2 validator framework: Issue helpers, run_all, and ValidationReport.

Every Phase 2 validator is a pure function

    (spec: SupportedBuild, catalog: Catalog, board: BoardModel) -> list[Issue]

Validators never raise for bad specs. They return Issue objects and only
``error`` severity blocks a spec.

``run_all(spec, catalog=None)`` resolves the catalog (and the board model) from
the Phase 1 loader when not injected, runs every validator, collects issues and
per-validator timing, and reports whether the spec passed (no errors).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from sparkplan.models.buildspec import SupportedBuild
from sparkplan.models.catalog import BoardModel, Catalog, get_catalog
from sparkplan.models.spec import Issue

ValidatorFn = Callable[[SupportedBuild, Catalog, BoardModel], list[Issue]]

_SEVERITY_ORDER: dict[str, int] = {"error": 0, "warning": 1, "info": 2}


def issue(
    code: str,
    message: str,
    *,
    severity: str = "error",
    part_ids: list[str] | None = None,
    fix_hint: str = "",
) -> Issue:
    """Construct an Issue with the repo-standard shape."""
    return Issue(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        message=message,
        part_ids=part_ids or [],
        fix_hint=fix_hint,
    )


def sort_issues(issues: list[Issue]) -> list[Issue]:
    return sorted(issues, key=lambda i: (_SEVERITY_ORDER[i.severity], i.code, i.message))


def has_errors(issues: list[Issue]) -> bool:
    return any(i.severity == "error" for i in issues)


def error_issues(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == "error"]


@dataclass
class ValidationReport:
    """Outcome of running every Phase 2 validator against one spec."""

    issues: list[Issue] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not has_errors(self.issues)

    @property
    def errors(self) -> list[Issue]:
        return error_issues(self.issues)

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    def by_code(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for i in self.issues:
            counts[i.code] = counts.get(i.code, 0) + 1
        return counts


def _validator_registry() -> list[tuple[str, ValidatorFn]]:
    from sparkplan.validators.phase2.behaviors import validate_behaviors
    from sparkplan.validators.phase2.completeness import validate_completeness
    from sparkplan.validators.phase2.conflicts import validate_conflicts
    from sparkplan.validators.phase2.i2c import validate_i2c
    from sparkplan.validators.phase2.layout import validate_layout
    from sparkplan.validators.phase2.logic_levels import validate_logic_levels
    from sparkplan.validators.phase2.membership import validate_membership
    from sparkplan.validators.phase2.pins_check import validate_pins
    from sparkplan.validators.phase2.power_check import validate_power

    return [
        ("V1_membership", validate_membership),
        ("V2_pins", validate_pins),
        ("V3_conflicts", validate_conflicts),
        ("V4_power", validate_power),
        ("V5_logic_levels", validate_logic_levels),
        ("V6_i2c", validate_i2c),
        ("V7_completeness", validate_completeness),
        ("V8_behaviors", validate_behaviors),
        ("V9_layout", validate_layout),
    ]


def run_all(spec: SupportedBuild, catalog: Catalog | None = None) -> ValidationReport:
    """Run every Phase 2 validator and return a ValidationReport.

    ``catalog`` defaults to the cached Phase 1 catalog; the board model is
    derived from the catalog's single board part.
    """
    cat = catalog if catalog is not None else get_catalog()
    board_part = cat.board()
    assert board_part.board is not None  # guaranteed by the catalog model
    board: BoardModel = board_part.board

    report = ValidationReport()
    for name, fn in _validator_registry():
        start = time.perf_counter()
        try:
            report.issues.extend(fn(spec, cat, board))
        except Exception as exc:  # a validator bug must not crash the pipeline
            report.issues.append(
                issue(
                    "VALIDATOR_CRASH",
                    f"{name} raised {type(exc).__name__}: {exc}",
                    fix_hint="This is a SparkPlan bug; report it.",
                )
            )
        report.timings_ms[name] = (time.perf_counter() - start) * 1000.0

    report.issues = sort_issues(report.issues)
    return report
