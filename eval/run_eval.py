"""Evaluation harness: run the planner over a case list and score it.

Usage:
    uv run python -m eval.run_eval                 # uses SPARKPLAN_LLM_* env
    uv run python -m eval.run_eval --out report.json

Deterministic cases (refusals via keyword prefilter) pass without any LLM.
Supported cases need a configured LLM; with FakeLLM you can script them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sparkplan.catalog import load_catalog
from sparkplan.llm.client import LLMClient, make_llm_from_env
from sparkplan.models.spec import PlanStatus
from sparkplan.planner import Planner

CASES_PATH = Path(__file__).parent / "cases.json"


def load_cases(path: str | Path = CASES_PATH) -> list[dict[str, Any]]:
    return json.loads(Path(path).read_text())


def run_eval(
    cases: list[dict[str, Any]],
    llm: LLMClient,
    catalog=None,
) -> dict[str, Any]:
    planner = Planner(llm, catalog or load_catalog())
    results: list[dict[str, Any]] = []
    passed = 0
    for case in cases:
        outcome = planner.plan(case["idea"])
        ok = _score(case, outcome)
        passed += int(ok)
        entry: dict[str, Any] = {
            "idea": case["idea"],
            "expect": case["expect"],
            "status": outcome.status.value,
            "pass": ok,
            "repair_rounds": outcome.repair_rounds,
        }
        if outcome.spec is not None:
            entry["parts"] = sorted({p.part_id for p in outcome.spec.parts})
        if outcome.refusal is not None:
            entry["reasons"] = outcome.refusal.reasons
        results.append(entry)
    return {"passed": passed, "total": len(cases), "cases": results}


def _score(case: dict[str, Any], outcome) -> bool:
    if case["expect"] == "refused":
        return outcome.status is PlanStatus.REFUSED
    if outcome.status is not PlanStatus.OK or outcome.spec is None:
        return False
    have = {p.part_id for p in outcome.spec.parts}
    groups = case.get("expected_parts_any_of")
    if not groups:
        return True
    return all(any(pid in have for pid in group) for group in groups)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SparkPlan eval harness")
    parser.add_argument("--cases", default=str(CASES_PATH))
    parser.add_argument("--out", default=None, help="write JSON report here")
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    report = run_eval(cases, make_llm_from_env())
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text)
    print(text)
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
