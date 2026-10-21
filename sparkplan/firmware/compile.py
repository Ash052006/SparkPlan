"""Compile wrapper with structured results.

The wrapper never marks a sketch as compiled unless ``arduino-cli`` really
compiled it.  It reports:

* ``passed``    - arduino-cli compiled the sketch (exit code 0).
* ``failed``    - the compile ran and failed, or the toolchain broke.
* ``skipped``   - a dependency was missing (no arduino-cli, no ESP32 core, a
  library that the index does not know).
* ``unsupported`` - the request cannot be compiled by this wrapper at all.

Every outcome carries machine-readable :class:`~sparkplan.models.spec.Issue`
objects so the caller can report them like validator findings.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sparkplan.models.spec import Issue

_COMPILE_TIMEOUT_S = 300

# Environment variable that carries extra arduino-cli build properties, for
# example "tools.ctags.pattern=/usr/bin/true".  Entries are separated by
# spaces or semicolons.
_BUILD_PROPERTIES_ENV = "SPARKPLAN_ARDUINO_BUILD_PROPERTIES"

# arduino-cli exits non-zero with these markers when a platform or a library
# is not installed.  They mean "not available", not "the sketch is wrong".
_MISSING_PLATFORM_MARKERS = (
    "platformnotfound",
    "platform 'esp32:esp32' not found",
    "platform 'esp32:esp32' is not installed",
    "not installed. install it",
    "platform 'esp32:esp32' not installed",
)
_MISSING_LIBRARY_MARKERS = (
    "library not found",
    "not found in the library index",
    "no libraries found for",
    "unknown library",
)


@dataclass
class CompileArtifacts:
    """The outcome of one compile attempt."""

    status: Literal["passed", "failed", "unsupported", "skipped"]
    issues: list[Issue] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    logs: str = ""
    missing: list[str] = field(default_factory=list)
    sketch_path: Path | None = None


def _detect_cli(cli_path: str | None = None) -> tuple[str | None, list[str]]:
    """Locate arduino-cli and report what is missing when it cannot."""
    cli = cli_path or os.environ.get("SPARKPLAN_ARDUINO_CLI") or shutil.which("arduino-cli")
    if not cli:
        return None, ["arduino-cli"]
    path = Path(cli)
    if not path.exists():
        return None, [f"arduino-cli ({cli})"]
    if not os.access(cli, os.X_OK):
        return None, [f"arduino-cli ({cli}, not executable)"]
    return cli, []


def _skipped(
    code: str,
    message: str,
    fix_hint: str,
    missing: list[str],
    logs: str = "",
) -> CompileArtifacts:
    return CompileArtifacts(
        status="skipped",
        issues=[
            Issue(
                code=code,
                severity="error",
                message=message,
                part_ids=[],
                fix_hint=fix_hint,
            )
        ],
        logs=logs or message,
        missing=missing,
    )


def _mentions(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _build_properties(explicit: list[str] | None) -> list[str]:
    """Extra arduino-cli build properties, from the call or the environment."""
    properties: list[str] = []
    sources: list[str] = list(explicit or [])
    sources.extend(os.environ.get(_BUILD_PROPERTIES_ENV, "").replace(";", " ").split())
    for item in sources:
        item = item.strip()
        if item and item not in properties:
            properties.append(item)
    return properties


def _parse_compile_issues(output: str) -> list[Issue]:
    """Turn compiler diagnostics into issues."""
    issues: list[Issue] = []
    for line in output.splitlines():
        lowered = line.lower()
        if "error:" in lowered:
            issues.append(
                Issue(
                    code="COMPILE_ERROR",
                    severity="error",
                    message=line.strip()[:300],
                    part_ids=[],
                    fix_hint="",
                )
            )
    return issues


def compile_sketch(
    sketch_dir: Path,
    fqbn: str = "esp32:esp32:esp32",
    cli_path: str | None = None,
    libraries: list[str] | None = None,
    timeout_s: int = _COMPILE_TIMEOUT_S,
    extra_lib_install: bool = True,
    build_properties: list[str] | None = None,
) -> CompileArtifacts:
    """Compile one sketch folder with arduino-cli.

    ``libraries`` are installed first when ``extra_lib_install`` is set; the
    entries may carry a pinned version (``"DHT sensor library@1.4.7"``).
    ``build_properties`` are passed to arduino-cli as ``--build-property``.
    """
    cli, missing = _detect_cli(cli_path)
    if not cli:
        return _skipped(
            "ARDUINO_CLI_MISSING",
            "arduino-cli not found; install it or set SPARKPLAN_ARDUINO_CLI",
            "Install arduino-cli and add it to PATH",
            missing,
        )

    log_parts: list[str] = []

    if extra_lib_install and libraries:
        try:
            install = subprocess.run(
                [cli, "lib", "install", *libraries],
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return CompileArtifacts(
                status="failed",
                issues=[
                    Issue(
                        code="LIB_INSTALL_TIMEOUT",
                        severity="error",
                        message="Library installation timed out",
                        part_ids=[],
                        fix_hint="Check the network and the library names",
                    )
                ],
                logs="\n".join(log_parts),
            )
        except OSError as exc:
            return CompileArtifacts(
                status="failed",
                issues=[
                    Issue(
                        code="LIB_INSTALL_ERROR",
                        severity="error",
                        message=f"Library installation failed: {exc}",
                        part_ids=[],
                        fix_hint="Check the arduino-cli installation",
                    )
                ],
                logs="\n".join(log_parts),
            )
        install_output = install.stdout + install.stderr
        log_parts.append(install_output)
        if install.returncode != 0:
            if _mentions(install_output, _MISSING_LIBRARY_MARKERS):
                return _skipped(
                    "LIBRARY_UNAVAILABLE",
                    f"arduino-cli could not find one of the required libraries: {libraries}",
                    "Check the library names and versions against the manifest",
                    list(libraries),
                    install_output,
                )
            return CompileArtifacts(
                status="failed",
                issues=[
                    Issue(
                        code="LIB_INSTALL_ERROR",
                        severity="error",
                        message=f"Library installation failed: {install_output.strip()[:300]}",
                        part_ids=[],
                        fix_hint="Check the arduino-cli lib install output",
                    )
                ],
                logs=install_output,
            )

    try:
        with tempfile.TemporaryDirectory() as build_dir:
            command = [cli, "compile", "--fqbn", fqbn, "--build-path", build_dir]
            for prop in _build_properties(build_properties):
                command += ["--build-property", prop]
            command.append(str(sketch_dir))
            proc = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            output = proc.stdout + proc.stderr
            log_parts.append(output)
            combined = "\n".join(log_parts)
            if proc.returncode == 0:
                return CompileArtifacts(
                    status="passed",
                    issues=[],
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    exit_code=proc.returncode,
                    logs=combined[-20000:],
                    sketch_path=Path(sketch_dir),
                )
            if _mentions(output, _MISSING_PLATFORM_MARKERS):
                return _skipped(
                    "ESP32_PLATFORM_MISSING",
                    "the ESP32 core is not installed for arduino-cli",
                    "arduino-cli core install esp32:esp32",
                    ["platform:esp32:esp32"],
                    combined,
                )
            return CompileArtifacts(
                status="failed",
                issues=_parse_compile_issues(output),
                stdout=proc.stdout,
                stderr=proc.stderr,
                exit_code=proc.returncode,
                logs=combined[-20000:],
                sketch_path=Path(sketch_dir),
            )
    except subprocess.TimeoutExpired:
        return CompileArtifacts(
            status="failed",
            issues=[
                Issue(
                    code="COMPILE_TIMEOUT",
                    severity="error",
                    message=f"Compilation timed out after {timeout_s} s",
                    part_ids=[],
                    fix_hint="Raise the timeout or compile on a machine with more memory",
                )
            ],
            logs="\n".join(log_parts),
        )
    except FileNotFoundError:
        return _skipped(
            "ARDUINO_CLI_MISSING",
            "arduino-cli not found",
            "Install arduino-cli and add it to PATH",
            ["arduino-cli"],
            "\n".join(log_parts),
        )
    except OSError as exc:
        return CompileArtifacts(
            status="failed",
            issues=[
                Issue(
                    code="COMPILE_ERROR",
                    severity="error",
                    message=str(exc),
                    part_ids=[],
                    fix_hint="",
                )
            ],
            logs="\n".join(log_parts) + f"\n{exc}",
        )
