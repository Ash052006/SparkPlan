"""Additive slicer verification for generated enclosure STLs.

PrusaSlicer is invoked as a real subprocess with a controlled, version-
controlled profile.  A successful process launch is never enough: the exit
code, output existence, and output size are all checked.  Slicing proves only
that PrusaSlicer produced G-code from the STL; it does not prove printability,
fit, strength, or material suitability.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from sparkplan.models.spec import Issue

SliceStatus = Literal["passed", "failed", "skipped", "unverified"]

DEFAULT_PROFILE = Path(__file__).with_name("prusaslicer.ini")
DEFAULT_SLICER = "prusa-slicer"
DEFAULT_TIMEOUT_S = 120.0
_DIAGNOSTIC_LIMIT = 4000
_VERSION_RE = re.compile(r"PrusaSlicer-([0-9][^\s]*)")
_SECRET_RE = re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd)\b(\s*[=:]\s*)(\S+)")


@dataclass
class SlicePartResult:
    """Slicer result for one STL part."""

    name: str
    status: SliceStatus
    input_path: str
    output_path: str | None = None
    command: list[str] = field(default_factory=list)
    exit_code: int | None = None
    duration_ms: float | None = None
    diagnostics: str = ""
    reason: str | None = None
    issues: list[Issue] = field(default_factory=list)


@dataclass
class SliceResult:
    """Slicer result for the base and lid together."""

    status: SliceStatus
    slicer: str | None = None
    version: str | None = None
    profile: str | None = None
    parts: dict[str, SlicePartResult] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    duration_ms: float | None = None
    reason: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "passed"


def _issue(
    code: str,
    message: str,
    *,
    severity: str = "error",
    fix_hint: str = "",
) -> Issue:
    return Issue(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        message=message,
        part_ids=[],
        fix_hint=fix_hint,
    )


def resolve_slicer(explicit: str | Path | None = None) -> str | None:
    """Resolve a usable PrusaSlicer executable, or return ``None``."""
    candidate = (
        str(explicit)
        if explicit is not None
        else os.environ.get("SPARKPLAN_SLICER", DEFAULT_SLICER)
    )
    found = shutil.which(candidate)
    if found:
        return found
    path = Path(candidate)
    if path.is_file() and os.access(path, os.X_OK):
        return str(path)
    return None


def slicer_version(slicer: str, *, timeout_s: float = 10.0) -> str | None:
    """Read the version from PrusaSlicer's own help output."""
    try:
        completed = subprocess.run(
            [slicer, "--help"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = f"{completed.stdout or ''}\n{completed.stderr or ''}"
    match = _VERSION_RE.search(output)
    return match.group(1) if match else None


def _resolve_profile(explicit: str | Path | None = None) -> Path | None:
    raw = explicit if explicit is not None else os.environ.get("SPARKPLAN_SLICER_PROFILE")
    profile = Path(raw) if raw is not None else DEFAULT_PROFILE
    if not profile.is_file() or profile.stat().st_size <= 0:
        return None
    return profile


def _input_issue(path: Path) -> Issue | None:
    if not path.is_file():
        return _issue(
            "SLICE_INPUT_MISSING",
            f"Slicer input does not exist: {path}",
            fix_hint="Regenerate the enclosure STL before requesting a slice.",
        )
    if path.stat().st_size <= 0:
        return _issue(
            "SLICE_INPUT_EMPTY",
            f"Slicer input is empty: {path}",
            fix_hint="Regenerate the enclosure STL before requesting a slice.",
        )
    return None


def _summarize(text: str, *, home: Path | None = None) -> str:
    if home is not None:
        text = text.replace(str(home), "~")
    text = _SECRET_RE.sub(r"\1\2[redacted]", text)
    text = text.strip()
    if len(text) > _DIAGNOSTIC_LIMIT:
        text = f"{text[:_DIAGNOSTIC_LIMIT]}\n... [truncated]"
    return text


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _run_part(
    *,
    name: str,
    input_path: Path,
    output_path: Path,
    slicer: str,
    profile: Path,
    datadir: Path,
    timeout_s: float,
) -> SlicePartResult:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    command = [
        slicer,
        "--export-gcode",
        "--output",
        str(output_path),
        "--load",
        str(profile),
        "--datadir",
        str(datadir),
        "--loglevel",
        "2",
        str(input_path),
    ]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration = (time.perf_counter() - started) * 1000.0
        diagnostics = _summarize(
            "\n".join((_text(exc.stdout), _text(exc.stderr))),
            home=Path.home(),
        )
        issue = _issue(
            "SLICE_TIMEOUT",
            f"PrusaSlicer timed out after {timeout_s:.0f} s on {input_path.name}.",
            fix_hint="Raise the slicer timeout or inspect the STL for extreme complexity.",
        )
        return SlicePartResult(
            name=name,
            status="failed",
            input_path=str(input_path),
            output_path=str(output_path),
            command=command,
            duration_ms=duration,
            diagnostics=diagnostics,
            reason=issue.message,
            issues=[issue],
        )
    except OSError as exc:
        duration = (time.perf_counter() - started) * 1000.0
        issue = _issue(
            "SLICE_LAUNCH_FAILED",
            f"Could not launch PrusaSlicer: {exc}",
            fix_hint="Verify that the slicer executable is runnable.",
        )
        return SlicePartResult(
            name=name,
            status="failed",
            input_path=str(input_path),
            output_path=str(output_path),
            command=command,
            duration_ms=duration,
            reason=issue.message,
            issues=[issue],
        )

    duration = (time.perf_counter() - started) * 1000.0
    diagnostics = _summarize(
        "\n".join((_text(completed.stdout), _text(completed.stderr))),
        home=Path.home(),
    )
    if completed.returncode != 0:
        issue = _issue(
            "SLICE_PROCESS_FAILED",
            f"PrusaSlicer exited with code {completed.returncode} on {input_path.name}.",
            fix_hint="Inspect the slicer diagnostics and the generated STL.",
        )
        return SlicePartResult(
            name=name,
            status="failed",
            input_path=str(input_path),
            output_path=str(output_path),
            command=command,
            exit_code=completed.returncode,
            duration_ms=duration,
            diagnostics=diagnostics,
            reason=issue.message,
            issues=[issue],
        )
    if not output_path.is_file():
        issue = _issue(
            "SLICE_OUTPUT_MISSING",
            f"PrusaSlicer exited 0 but did not create {output_path}.",
            fix_hint="Inspect the slicer diagnostics; a zero exit code alone is not proof.",
        )
        return SlicePartResult(
            name=name,
            status="failed",
            input_path=str(input_path),
            output_path=str(output_path),
            command=command,
            exit_code=completed.returncode,
            duration_ms=duration,
            diagnostics=diagnostics,
            reason=issue.message,
            issues=[issue],
        )
    if output_path.stat().st_size <= 0:
        issue = _issue(
            "SLICE_OUTPUT_EMPTY",
            f"PrusaSlicer exited 0 but wrote an empty G-code file: {output_path}",
            fix_hint="Inspect the slicer diagnostics; a zero exit code alone is not proof.",
        )
        return SlicePartResult(
            name=name,
            status="failed",
            input_path=str(input_path),
            output_path=str(output_path),
            command=command,
            exit_code=completed.returncode,
            duration_ms=duration,
            diagnostics=diagnostics,
            reason=issue.message,
            issues=[issue],
        )
    return SlicePartResult(
        name=name,
        status="passed",
        input_path=str(input_path),
        output_path=str(output_path),
        command=command,
        exit_code=completed.returncode,
        duration_ms=duration,
        diagnostics=diagnostics,
    )


def verify_slice(
    base_stl: str | Path,
    lid_stl: str | Path,
    *,
    output_dir: str | Path | None = None,
    slicer: str | Path | None = None,
    profile: str | Path | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    enabled: bool = True,
) -> SliceResult:
    """Slice the base and lid with PrusaSlicer and verify both outputs.

    ``enabled=False`` returns a ``skipped`` result without reading the STLs.
    A requested run is ``unverified`` when the slicer is unavailable, and
    ``failed`` for invalid inputs, bad configuration, timeout, nonzero exit,
    or missing/empty output.
    """
    if not enabled:
        return SliceResult(
            status="skipped",
            reason="slicing was not requested",
        )

    base_path = Path(base_stl)
    lid_path = Path(lid_stl)
    inputs = {"base": base_path, "lid": lid_path}
    parts: dict[str, SlicePartResult] = {}
    issues: list[Issue] = []
    for name, path in inputs.items():
        input_issue = _input_issue(path)
        if input_issue is None:
            continue
        parts[name] = SlicePartResult(
            name=name,
            status="failed",
            input_path=str(path),
            reason=input_issue.message,
            issues=[input_issue],
        )
        issues.append(input_issue)

    if issues:
        for name, path in inputs.items():
            if name not in parts:
                parts[name] = SlicePartResult(
                    name=name,
                    status="skipped",
                    input_path=str(path),
                    reason="another slicer input was invalid",
                )
        return SliceResult(
            status="failed",
            parts=parts,
            issues=issues,
            reason="one or more slicer inputs are invalid",
        )

    resolved_profile = _resolve_profile(profile)
    if resolved_profile is None:
        issue = _issue(
            "SLICE_PROFILE_MISSING",
            "The controlled PrusaSlicer profile is missing or empty.",
            fix_hint="Restore sparkplan/geometry/prusaslicer.ini.",
        )
        return SliceResult(
            status="failed",
            profile=str(profile) if profile is not None else str(DEFAULT_PROFILE),
            parts={
                name: SlicePartResult(
                    name=name,
                    status="failed",
                    input_path=str(path),
                    reason=issue.message,
                    issues=[issue],
                )
                for name, path in inputs.items()
            },
            issues=[issue],
            reason=issue.message,
        )

    resolved_slicer = resolve_slicer(slicer)
    if resolved_slicer is None:
        issue = _issue(
            "SLICER_UNAVAILABLE",
            "PrusaSlicer is not installed or is not executable.",
            fix_hint="Install prusa-slicer or set SPARKPLAN_SLICER to its executable.",
        )
        return SliceResult(
            status="unverified",
            profile=str(resolved_profile),
            parts={
                name: SlicePartResult(
                    name=name,
                    status="unverified",
                    input_path=str(path),
                    reason=issue.message,
                    issues=[issue],
                )
                for name, path in inputs.items()
            },
            issues=[issue],
            reason=issue.message,
        )

    version = slicer_version(resolved_slicer)
    output_root = Path(output_dir) if output_dir is not None else base_path.parent
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="sparkplan-slicer-") as datadir:
        for name, path in inputs.items():
            parts[name] = _run_part(
                name=name,
                input_path=path,
                output_path=output_root / f"{path.stem}.gcode",
                slicer=resolved_slicer,
                profile=resolved_profile,
                datadir=Path(datadir),
                timeout_s=timeout_s,
            )
    duration = (time.perf_counter() - started) * 1000.0

    parts_issues = [issue for part in parts.values() for issue in part.issues]
    if any(part.status == "failed" for part in parts.values()):
        status: SliceStatus = "failed"
    elif any(part.status == "unverified" for part in parts.values()):
        status = "unverified"
    elif all(part.status == "passed" for part in parts.values()):
        status = "passed"
    else:
        status = "failed"
    reason = None
    if status == "failed":
        reason = next(
            (part.reason for part in parts.values() if part.status == "failed"),
            "slicing failed",
        )
    return SliceResult(
        status=status,
        slicer=resolved_slicer,
        version=version,
        profile=str(resolved_profile),
        parts=parts,
        issues=parts_issues,
        duration_ms=duration,
        reason=reason,
    )


def slice_details(result: SliceResult, *, requested: bool) -> dict[str, Any]:
    """Trace-friendly details for a slicing result."""
    return {
        "requested": requested,
        "slicer": result.slicer,
        "version": result.version,
        "profile": result.profile,
        "reason": result.reason,
        "parts": {
            name: {
                "status": part.status,
                "input": part.input_path,
                "output": part.output_path,
                "exit_code": part.exit_code,
                "duration_ms": part.duration_ms,
                "diagnostics": part.diagnostics,
            }
            for name, part in result.parts.items()
        },
    }
