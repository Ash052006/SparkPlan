"""Generate one enclosure and run the real pinned slicer in CI."""

from __future__ import annotations

import json
import shlex
import shutil
import sys
from pathlib import Path

from sparkplan.geometry import generate_enclosure, verify_slice
from sparkplan.models.buildspec import parse_build_spec

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "build" / "slicing"
FIXTURE = ROOT / "tests" / "fixtures" / "phase3" / "supported" / "soil_monitor.json"


def main() -> int:
    spec = parse_build_spec(json.loads(FIXTURE.read_text(encoding="utf-8")))
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    geometry = generate_enclosure(spec, out_dir=OUT_DIR)
    if not geometry.ok:
        for issue in geometry.issues:
            print(f"{issue.severity}: {issue.code}: {issue.message}")
        print("geometry failed; not slicing")
        return 1

    base = OUT_DIR / "base.stl"
    lid = OUT_DIR / "lid.stl"
    print(f"generated base: {base} ({base.stat().st_size} bytes)")
    print(f"generated lid:  {lid} ({lid.stat().st_size} bytes)")

    result = verify_slice(base, lid, output_dir=OUT_DIR, timeout_s=180.0)
    print(f"slicer: {result.slicer}")
    print(f"version: {result.version}")
    print(f"profile: {result.profile}")
    print(f"status: {result.status}")
    for name, part in result.parts.items():
        print(f"{name}: {part.status} exit={part.exit_code}")
        if part.command:
            print(f"  command: {shlex.join(part.command)}")
        if part.output_path is not None:
            output = Path(part.output_path)
            size = output.stat().st_size if output.is_file() else 0
            print(f"  output: {output} ({size} bytes)")
        if part.diagnostics:
            print(f"  diagnostics: {part.diagnostics}")
    for issue in result.issues:
        print(f"{issue.severity}: {issue.code}: {issue.message}")

    if result.status == "unverified":
        print("PrusaSlicer is unavailable; slicing was not verified")
        return 2
    if result.status != "passed":
        print("slicing verification failed")
        return 1
    print("both base and lid sliced to nonempty G-code")
    return 0


if __name__ == "__main__":
    sys.exit(main())
