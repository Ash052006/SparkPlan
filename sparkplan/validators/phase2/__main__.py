"""CLI: python -m sparkplan.validators.phase2 spec.json

Loads a BuildSpec JSON file, runs every Phase 2 validator, and prints a readable
report. Exit code 0 when the spec passes (no errors), 1 otherwise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sparkplan.models.buildspec import SupportedBuild, UnsupportedBuild, parse_build_spec
from sparkplan.validators.phase2.report import ValidationReport, run_all

_SEVERITY_TAG = {"error": "ERROR", "warning": "WARN ", "info": "INFO "}


def _format(report: ValidationReport) -> str:
    lines: list[str] = []
    status = "PASS" if report.passed else "FAIL"
    lines.append(f"Validation: {status}")
    lines.append(f"  errors:   {len(report.errors)}")
    lines.append(f"  warnings: {len(report.warnings)}")
    lines.append("")
    if report.issues:
        for issue in report.issues:
            tag = _SEVERITY_TAG.get(issue.severity, issue.severity.upper())
            parts = f" [{', '.join(issue.part_ids)}]" if issue.part_ids else ""
            lines.append(f"{tag} {issue.code}{parts}: {issue.message}")
            if issue.fix_hint:
                lines.append(f"        fix: {issue.fix_hint}")
    else:
        lines.append("No issues.")
    lines.append("")
    lines.append("Timing (ms):")
    for name, ms in report.timings_ms.items():
        lines.append(f"  {name}: {ms:.3f}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m sparkplan.validators.phase2 spec.json", file=sys.stderr)
        return 2

    path = Path(args[0])
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: could not read {path}: {exc}", file=sys.stderr)
        return 2

    spec = parse_build_spec(data)
    if isinstance(spec, UnsupportedBuild):
        print("This spec is an 'unsupported' outcome; nothing to validate.")
        print(f"reasons: {spec.reasons}")
        return 0

    assert isinstance(spec, SupportedBuild)
    report = run_all(spec)
    print(_format(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
