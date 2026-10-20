"""Validator framework.

Every check is a pure function (BuildSpec, Catalog) -> list[Issue].
Validators never raise for bad specs - bad input produces Issues.
`error_issues` are the only things that block a spec.
"""

from __future__ import annotations

from collections.abc import Callable

from sparkplan.catalog import Catalog
from sparkplan.models.spec import BuildSpec, Issue, Severity

ValidatorFn = Callable[[BuildSpec, Catalog], list[Issue]]

_SEVERITY_ORDER: dict[Severity, int] = {"error": 0, "warning": 1, "info": 2}


def sort_issues(issues: list[Issue]) -> list[Issue]:
    return sorted(issues, key=lambda i: (_SEVERITY_ORDER[i.severity], i.code, i.message))


def error_issues(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == "error"]


def has_errors(issues: list[Issue]) -> bool:
    return any(i.severity == "error" for i in issues)


def issues_to_machine(issues: list[Issue]) -> list[dict]:
    """Compact machine-readable form fed back to the LLM during repair."""
    return [
        {
            "code": i.code,
            "severity": i.severity,
            "message": i.message,
            "part_ids": i.part_ids,
            "fix_hint": i.fix_hint,
        }
        for i in sort_issues(issues)
    ]


def run_validators(spec: BuildSpec, catalog: Catalog) -> list[Issue]:
    from sparkplan.validators.actions import validate_actions
    from sparkplan.validators.pins import validate_connections
    from sparkplan.validators.power import validate_power
    from sparkplan.validators.structure import validate_structure

    checks: list[ValidatorFn] = [
        validate_structure,
        validate_connections,
        validate_power,
        validate_actions,
    ]
    issues: list[Issue] = []
    for check in checks:
        issues.extend(check(spec, catalog))
    return sort_issues(issues)
