#!/usr/bin/env python
"""Run the real Phase 3 planner on one idea.

Usage:
    python scripts/try_plan.py "a desk plant monitor"

Reads LLM credentials from SPARKPLAN_LLM_* env vars (see .env.example).
Never prints or logs the API key. Exit codes: 0 ok/unsupported, 1 failed,
2 usage or configuration error.

Live result - this performs a real network call.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from sparkplan.llm.phase3.client import LLMError, make_llm_from_env
from sparkplan.models.catalog import get_catalog
from sparkplan.planner.phase3.models import PlanResult, PlanStatus
from sparkplan.planner.phase3.planner import Planner


def _load_dotenv(path: Path) -> None:
    """Load SPARKPLAN_* keys from a .env file without printing them.

    Minimal stdlib parser; existing environment variables win. Never logs or
    echoes any value.
    """
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def _print_report(result: PlanResult) -> None:
    print(f"status      : {result.status.value}")
    if result.title:
        print(f"title       : {result.title}")
    print(f"llm_calls   : {result.llm_calls}")
    print(f"repair_round: {result.repair_rounds}")
    print(f"est_request : ~{result.estimated_request_tokens} tokens (last call, local estimate)")
    usage = "known" if result.total_prompt_tokens or result.total_completion_tokens else "unknown"
    print(
        f"tokens      : prompt={result.total_prompt_tokens} "
        f"completion={result.total_completion_tokens} ({usage})"
    )
    if result.attempts:
        print("attempts    :")
        for a in result.attempts:
            counts = (
                f"prompt={a.prompt_tokens} completion={a.completion_tokens}"
                if a.usage_present
                else "usage=unknown"
            )
            print(f"  round {a.round}: {a.kind} ({counts}) {a.detail}".rstrip())

    if result.status is PlanStatus.OK and result.spec is not None:
        print("\n--- BuildSpec ---")
        print(result.spec.model_dump_json(indent=2))

    if result.status is PlanStatus.UNSUPPORTED:
        print("\n--- Unsupported ---")
        print(
            json.dumps(
                {
                    "reasons": result.reasons,
                    "missing_capabilities": result.missing_capabilities,
                    "closest_supported_alternative": result.closest_supported_alternative,
                },
                indent=2,
            )
        )

    if result.status in (PlanStatus.FAILED, PlanStatus.REFUSED):
        print("\n--- Reasons ---")
        for r in result.reasons:
            print(f"- {r}")

    report = result.report
    if report is not None:
        print("\n--- Validation report ---")
        print(f"passed   : {report.passed}")
        print(f"errors   : {len(report.errors)}")
        print(f"warnings : {len(report.warnings)}")
        for i in report.issues:
            print(f"  [{i.severity}] {i.code}: {i.message}")
            if i.fix_hint:
                print(f"      fix: {i.fix_hint}")
    elif result.issues:
        print("\n--- Issues ---")
        for i in result.issues:
            print(f"  [{i.severity}] {i.code}: {i.message}")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print('usage: python scripts/try_plan.py "<idea>"', file=sys.stderr)
        return 2
    idea = argv[1]

    _load_dotenv(Path(__file__).resolve().parents[1] / ".env")

    try:
        llm = make_llm_from_env()
    except LLMError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    catalog = get_catalog()
    result = Planner(llm, catalog).plan(idea)

    print(f"idea: {idea!r}\n")
    _print_report(result)

    if result.status in (PlanStatus.OK, PlanStatus.UNSUPPORTED):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
