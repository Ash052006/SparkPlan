"""Real Wokwi CLI smoke tests for required fixtures.

Uses FakeLLM for planning, real arduino-cli/geometry, and actual wokwi-cli.
Fails immediately if wokwi-cli or WOKWI_CLI_TOKEN are missing. Never prints
or persists tokens.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

from sparkplan.llm.phase3 import FakeLLM
from sparkplan.models.catalog import get_catalog
from sparkplan.pipeline.run import run
from sparkplan.pipeline.run_paths import RunPaths

FIXTURES = [
    ("motion_led", "tests/fixtures/phase3/supported/motion_led.json"),
    ("buzzer_series_resistor", "tests/fixtures/phase3/supported/buzzer_series_resistor.json"),
]


def _detect_cli() -> str | None:
    configured = os.environ.get("SPARKPLAN_WOKWI_CLI") or shutil.which("wokwi-cli")
    if not configured:
        return None
    path = Path(configured)
    if not path.is_file() or not os.access(path, os.X_OK):
        return None
    return str(path)


def _check_token() -> bool:
    token = os.environ.get("WOKWI_CLI_TOKEN", "")
    if not token:
        return False
    return token.startswith("wok_") and len(token) == 44


def _load_spec(path: Path):
    from sparkplan.models.buildspec import parse_build_spec

    data = json.loads(path.read_text())
    spec = parse_build_spec(data)
    return spec


def main() -> int:
    cli = _detect_cli()
    if cli is None:
        print("ERROR: wokwi-cli not found or not executable", file=sys.stderr)
        print("Set SPARKPLAN_WOKWI_CLI or add wokwi-cli to PATH", file=sys.stderr)
        return 1
    if not _check_token():
        print("ERROR: WOKWI_CLI_TOKEN missing or malformed", file=sys.stderr)
        print("Set a valid token (starts with wok_, 44 chars).", file=sys.stderr)
        print("Never commit tokens.", file=sys.stderr)
        return 1

    catalog = get_catalog()
    failures = 0
    results = []

    for name, fixture_rel in FIXTURES:
        fixture_path = Path(fixture_rel)
        if not fixture_path.is_file():
            print(f"ERROR: fixture not found: {fixture_path}", file=sys.stderr)
            return 1
        spec = _load_spec(fixture_path)
        spec_dict = spec.model_dump(mode="json")
        run_id = f"wokwi-smoke-{name}-{int(time.time())}"
        runs_root = Path(os.environ.get("SPARKPLAN_RUNS_DIR") or "runs")
        paths = RunPaths(run_id, runs_root)
        print(f"=== {name} ===")
        print(f"run_id: {run_id}")
        try:
            result = run(
                spec.title,
                FakeLLM(script=[spec_dict]),
                run_id=run_id,
                runs_root=runs_root,
                catalog=catalog,
                do_slice=False,
                do_compile=True,
                do_simulate=True,
                wokwi_cli=cli,
                wokwi_timeout_ms=30000,
            )
        except Exception as exc:
            print(f"EXCEPTION: {exc}", file=sys.stderr)
            failures += 1
            results.append((name, "exception", str(exc)))
            continue

        sim_stage = result.stage("simulation")
        sim_status = sim_stage.status.value if sim_stage else "missing"
        verdict = sim_stage.evidence.get("simulation_status") if sim_stage else None
        print(f"run_status: {result.status.value}")
        print(f"simulation_stage: {sim_status}")
        print(f"simulation_verdict: {verdict}")
        print(f"cli_exit: {sim_stage.evidence.get('exit_code') if sim_stage else None}")
        if sim_stage and sim_stage.reason:
            print(f"simulation_reason: {sim_stage.reason}")
        if sim_stage:
            for key in ("unsupported_instances", "boot_marker_seen", "screenshot_verified"):
                if key in sim_stage.evidence:
                    print(f"{key}: {sim_stage.evidence[key]}")
        for art in ("simulation_log", "simulation_screenshot", "simulation_diagram"):
            if art in result.artifacts:
                p = paths.artifact(art) if paths.run_dir.exists() else None
                if p and p.exists():
                    print(f"artifact_{art}: {p} ({p.stat().st_size} bytes)")
                else:
                    print(f"artifact_{art}: present in report")
            else:
                print(f"artifact_{art}: MISSING")
        if sim_stage and sim_stage.status.value == "passed":
            print("RESULT: PASS")
        elif sim_stage and sim_stage.status.value == "unverified":
            print("RESULT: UNVERIFIED")
            failures += 1
        else:
            print("RESULT: FAIL")
            failures += 1
        results.append((name, sim_stage.status.value if sim_stage else "missing", verdict))

    print("\n=== SUMMARY ===")
    for r in results:
        print(r)
    if failures:
        print(f"FAILURES: {failures}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
