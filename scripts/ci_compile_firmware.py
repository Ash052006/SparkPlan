#!/usr/bin/env python
"""CI script: generate and compile every fixture SparkPlan claims to support.

Two fixture families are compiled:

* ``tests/fixtures/valid/*.json`` - legacy build specs with actions, rendered
  with the split catalog (``catalog/parts/*.json``);
* ``tests/fixtures/phase3/supported/*.json`` and
  ``tests/fixtures/phase2/v8/valid.json`` - behaviour based builds, rendered
  with the Phase 2+ catalog (``catalog/parts.json``).

arduino-cli requires the main file of a sketch to be named after its folder,
so every sketch is written as ``<folder>/<folder>.ino``.  The pinned library
manifest is written next to the sketch.

The script never touches the network for anything but the arduino-cli library
install; without arduino-cli it reports "skipped" and fails, so a CI run
without a toolchain can never look like a successful compile.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from sparkplan.catalog.loader import load_catalog as load_legacy_catalog
from sparkplan.firmware import compile_sketch, generate_firmware
from sparkplan.firmware.manifest import resolve_libraries, write_manifest
from sparkplan.models.buildspec import SupportedBuild
from sparkplan.models.catalog import load_catalog as load_catalog_v2
from sparkplan.models.spec import BuildSpec

FQBN = "esp32:esp32:esp32"


def _cli_path() -> str:
    return os.environ.get("SPARKPLAN_ARDUINO_CLI") or shutil.which("arduino-cli") or ""


def _toolchain_header() -> str:
    """The toolchain that produced a log, so a compile is self-describing."""
    lines = [f"# fqbn: {FQBN}"]
    cli = _cli_path()
    if not cli:
        return "\n".join(lines) + "\n"
    for args in (["version"], ["core", "list"]):
        try:
            proc = subprocess.run(  # noqa: S603
                [cli, *args], capture_output=True, text=True, timeout=60
            )
        except (OSError, subprocess.SubprocessError):
            continue
        output = (proc.stdout + proc.stderr).strip()
        if output:
            lines.append(f"# {output}")
    return "\n".join(lines) + "\n"


def _write_sketch(sketch_dir: Path, sketch: str) -> Path:
    """Write the sketch; the .ino file must be named after its folder."""
    sketch_dir.mkdir(parents=True, exist_ok=True)
    path = sketch_dir / f"{sketch_dir.name}.ino"
    path.write_text(sketch, encoding="utf-8")
    return path


def _compile_one(
    name: str,
    sketch: str,
    libraries: list[str],
    sketch_dir: Path,
) -> tuple[bool, str]:
    """Compile one sketch and return ``(ok, log)``."""
    _write_sketch(sketch_dir, sketch)
    resolution = resolve_libraries(libraries)
    if resolution.unknown:
        return False, f"unpinned libraries: {', '.join(resolution.unknown)}"
    write_manifest(sketch_dir, libraries)
    artifacts = compile_sketch(
        sketch_dir,
        fqbn=FQBN,
        libraries=list(resolution.specs),
    )
    header = _toolchain_header()
    (sketch_dir / "compile.log").write_text(header + artifacts.logs, encoding="utf-8")
    if artifacts.status != "passed":
        codes = ", ".join(issue.code for issue in artifacts.issues) or artifacts.status
        return False, f"{artifacts.status} ({codes})"
    return True, artifacts.logs


def _legacy_fixtures(root: Path) -> list[tuple[str, Path]]:
    fixtures = root / "tests" / "fixtures" / "valid"
    if not fixtures.is_dir():
        return []
    return [(path.stem, path) for path in sorted(fixtures.glob("*.json"))]


def _behaviour_fixtures(root: Path) -> list[tuple[str, Path]]:
    paths: list[Path] = []
    phase3 = root / "tests" / "fixtures" / "phase3" / "supported"
    if phase3.is_dir():
        paths.extend(sorted(phase3.glob("*.json")))
    phase2 = root / "tests" / "fixtures" / "phase2" / "v8" / "valid.json"
    if phase2.is_file():
        paths.append(phase2)
    return [(path.stem, path) for path in paths]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    out_dir = root / "build" / "firmware"
    out_dir.mkdir(parents=True, exist_ok=True)

    legacy_catalog = load_legacy_catalog()
    catalog_v2 = load_catalog_v2()

    failures: list[tuple[str, str]] = []
    compiled: list[str] = []

    for name, path in _legacy_fixtures(root):
        try:
            spec = BuildSpec.model_validate(json.loads(path.read_text()))
        except Exception as exc:
            print(f"SKIP_INVALID {path}: {exc}", file=sys.stderr)
            failures.append((str(path), f"invalid: {exc}"))
            continue
        try:
            result = generate_firmware(spec, legacy_catalog)
        except Exception as exc:
            print(f"GEN_FAIL {path}: {exc}", file=sys.stderr)
            failures.append((str(path), f"generate: {exc}"))
            continue
        ok, log = _compile_one(name, result.sketch, result.libraries, out_dir / name)
        if ok:
            print(f"OK {name}")
            compiled.append(name)
        else:
            print(f"COMPILE_FAIL {name}: {log}", file=sys.stderr)
            failures.append((name, f"compile: {log}"))

    for name, path in _behaviour_fixtures(root):
        try:
            spec = SupportedBuild.model_validate(json.loads(path.read_text()))
        except Exception as exc:
            print(f"SKIP_INVALID {path}: {exc}", file=sys.stderr)
            failures.append((str(path), f"invalid: {exc}"))
            continue
        try:
            result = generate_firmware(spec, catalog_v2)
        except Exception as exc:
            print(f"GEN_FAIL {path}: {exc}", file=sys.stderr)
            failures.append((str(path), f"generate: {exc}"))
            continue
        ok, log = _compile_one(name, result.sketch, result.libraries, out_dir / name)
        if ok:
            print(f"OK {name}")
            compiled.append(name)
        else:
            print(f"COMPILE_FAIL {name}: {log}", file=sys.stderr)
            failures.append((name, f"compile: {log}"))

    if failures:
        print("FAILURES:", file=sys.stderr)
        for name, reason in failures:
            print(f"  {name}: {reason}", file=sys.stderr)
        return 1
    print(f"All {len(compiled)} supported fixtures compiled successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
