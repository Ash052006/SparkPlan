"""Run one generated Wokwi project through the Wokwi CLI.

The runner is deliberately conservative:

* a missing CLI or token is ``skipped``, never a success;
* a malformed token, authentication failure, timeout, crash, missing log, or
  missing screenshot is ``failed``;
* a real run with omitted components is ``partial``;
* ``passed`` requires an exit code of zero, a serial log containing the boot
  marker, and a non-empty screenshot.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import threading
import time
import zipfile
from pathlib import Path

from sparkplan.models.spec import Issue
from sparkplan.simulation.models import SimulationResult, SimulationStatus, WokwiProject
from sparkplan.simulation.project import SCENARIO_MARKER, SERIAL_LOG_NAME

DEFAULT_TIMEOUT_MS = 30000
DEFAULT_PROCESS_TIMEOUT_S = 60.0
DEFAULT_QUEUE_TIMEOUT_S = 120.0

_SIMULATION_SLOT = threading.BoundedSemaphore(1)
_TOKEN_RE = re.compile(r"^wok_\S{40}$")
_AUTH_MARKERS = (
    "unauthorized",
    "forbidden",
    "invalid token",
    "token is not valid",
    "authentication failed",
    "wokwi_cli_token",
)


def _issue(code: str, message: str, *, severity: str = "error", fix_hint: str = "") -> Issue:
    return Issue(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        message=message,
        part_ids=[],
        fix_hint=fix_hint,
    )


def _detect_cli(cli_path: str | Path | None) -> tuple[str | None, str | None]:
    configured = cli_path or os.environ.get("SPARKPLAN_WOKWI_CLI") or shutil.which("wokwi-cli")
    if not configured:
        return None, "wokwi-cli was not found"
    path = Path(configured)
    if not path.is_file():
        return None, f"wokwi-cli does not exist: {path}"
    if not os.access(path, os.X_OK):
        return None, f"wokwi-cli is not executable: {path}"
    return str(path), None


def _token() -> tuple[str | None, str | None]:
    token = os.environ.get("WOKWI_CLI_TOKEN", "")
    if not token:
        return None, "WOKWI_CLI_TOKEN is not set"
    if not _TOKEN_RE.fullmatch(token):
        return None, "WOKWI_CLI_TOKEN is malformed (expected wok_ followed by 40 characters)"
    return token, None


def _redact(text: str, token: str | None) -> str:
    if token:
        text = text.replace(token, "[redacted]")
    return text


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _command(
    cli: str,
    project: WokwiProject,
    *,
    timeout_ms: int,
    serial_log: Path,
) -> list[str]:
    return [
        cli,
        str(project.project_dir),
        "--scenario",
        project.scenario_path.name,
        "--serial-log-file",
        str(serial_log),
        "--timeout",
        str(timeout_ms),
        "--timeout-exit-code",
        "42",
        "--fail-text",
        "Guru Meditation",
    ]


def _run_process(
    command: list[str],
    *,
    cwd: Path,
    process_timeout_s: float,
) -> tuple[int | None, str, str, bool]:
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=process_timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        stdout, stderr = proc.communicate()
    return proc.returncode, stdout, stderr, timed_out


def run_simulation(
    project: WokwiProject,
    *,
    cli_path: str | Path | None = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    process_timeout_s: float | None = None,
    queue_timeout_s: float = DEFAULT_QUEUE_TIMEOUT_S,
) -> SimulationResult:
    """Execute a generated Wokwi project and classify the outcome honestly."""
    project_issues = list(project.issues)
    if project.error_issues:
        reason = project.error_issues[0].message
        return SimulationResult(
            status=SimulationStatus.FAILED,
            reason=reason,
            issues=project_issues,
            project=project,
        )

    cli, cli_error = _detect_cli(cli_path)
    if cli is None or cli_error is not None:
        issue = _issue(
            "SIMULATION_CLI_MISSING",
            cli_error or "wokwi-cli was not found",
            fix_hint="Install wokwi-cli and add it to PATH or set SPARKPLAN_WOKWI_CLI.",
        )
        return SimulationResult(
            status=SimulationStatus.SKIPPED,
            reason=issue.message,
            issues=[*project_issues, issue],
            project=project,
        )

    token, token_error = _token()
    if token_error is not None:
        invalid = token_error.startswith("WOKWI_CLI_TOKEN is malformed")
        issue = _issue(
            "SIMULATION_TOKEN_INVALID" if invalid else "SIMULATION_TOKEN_MISSING",
            token_error,
            fix_hint=(
                "Set a valid Wokwi CI token in WOKWI_CLI_TOKEN."
                if invalid
                else "Set WOKWI_CLI_TOKEN to run simulations; never commit the token."
            ),
        )
        return SimulationResult(
            status=SimulationStatus.FAILED if invalid else SimulationStatus.SKIPPED,
            reason=issue.message,
            issues=[*project_issues, issue],
            project=project,
        )

    if not _SIMULATION_SLOT.acquire(timeout=queue_timeout_s):
        issue = _issue(
            "SIMULATION_QUEUE_TIMEOUT",
            f"timed out after {queue_timeout_s:g}s waiting for the simulation slot",
            fix_hint="Retry later or raise SPARKPLAN_WOKWI_QUEUE_TIMEOUT_S.",
        )
        return SimulationResult(
            status=SimulationStatus.FAILED,
            reason=issue.message,
            issues=[*project_issues, issue],
            project=project,
        )

    started = time.perf_counter()
    try:
        serial_log = project.project_dir / SERIAL_LOG_NAME
        try:
            serial_log.unlink(missing_ok=True)
            project.screenshot_path.unlink(missing_ok=True)
        except OSError:
            pass
        command = _command(cli, project, timeout_ms=timeout_ms, serial_log=serial_log)
        try:
            effective_timeout_s = (
                process_timeout_s
                if process_timeout_s is not None
                else max(DEFAULT_PROCESS_TIMEOUT_S, timeout_ms / 1000.0 + 30.0)
            )
            exit_code, stdout, stderr, timed_out = _run_process(
                command,
                cwd=project.project_dir,
                process_timeout_s=effective_timeout_s,
            )
        except OSError as exc:
            issue = _issue(
                "SIMULATION_EXEC_FAILED",
                f"wokwi-cli could not be started: {exc}",
                fix_hint="Check the wokwi-cli installation and permissions.",
            )
            return SimulationResult(
                status=SimulationStatus.FAILED,
                reason=issue.message,
                issues=[*project_issues, issue],
                project=project,
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )
        duration_ms = (time.perf_counter() - started) * 1000.0
        serial_text = _read_text(serial_log)
        boot_marker_seen = SCENARIO_MARKER in serial_text or SCENARIO_MARKER in stdout
        screenshot_verified = (
            project.screenshot_path.is_file() and project.screenshot_path.stat().st_size > 0
        )
        stdout = _redact(stdout, token)
        stderr = _redact(stderr, token)
        serial_text = _redact(serial_text, token)

        runtime_issues: list[Issue] = []
        combined = f"{stdout}\n{stderr}".lower()
        auth_failed = any(marker in combined for marker in _AUTH_MARKERS)
        if timed_out:
            runtime_issues.append(
                _issue(
                    "SIMULATION_TIMEOUT",
                    f"wokwi-cli exceeded the {effective_timeout_s:g}s process timeout",
                    fix_hint="Inspect the serial log or raise the simulation timeout.",
                )
            )
        elif auth_failed:
            runtime_issues.append(
                _issue(
                    "SIMULATION_AUTH_FAILED",
                    "Wokwi rejected the CLI token; the simulation did not run",
                    fix_hint="Check that WOKWI_CLI_TOKEN is active and has CI access.",
                )
            )
        elif exit_code != 0:
            runtime_issues.append(
                _issue(
                    "SIMULATION_EXEC_FAILED",
                    f"wokwi-cli exited with code {exit_code}",
                    fix_hint="Inspect the serial log and wokwi-cli output.",
                )
            )
        if not serial_log.is_file() or not serial_text.strip():
            runtime_issues.append(
                _issue(
                    "SIMULATION_SERIAL_MISSING",
                    "wokwi-cli did not produce a non-empty serial log",
                    fix_hint="Check the wokwi-cli invocation and project configuration.",
                )
            )
        elif not boot_marker_seen:
            runtime_issues.append(
                _issue(
                    "SIMULATION_BOOT_MARKER_MISSING",
                    f"the serial log does not contain the boot marker {SCENARIO_MARKER!r}",
                    fix_hint="Inspect the serial log; the firmware may not have booted.",
                )
            )
        if not screenshot_verified:
            runtime_issues.append(
                _issue(
                    "SIMULATION_SCREENSHOT_MISSING",
                    "the scenario did not produce a non-empty screenshot",
                    fix_hint="Inspect the scenario and Wokwi diagram.",
                )
            )

        all_issues = [*project_issues, *runtime_issues]
        if runtime_issues:
            return SimulationResult(
                status=SimulationStatus.FAILED,
                reason=runtime_issues[0].message,
                issues=all_issues,
                project=project,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                duration_ms=duration_ms,
                serial_text=serial_text,
                boot_marker_seen=boot_marker_seen,
                screenshot_verified=screenshot_verified,
                timed_out=timed_out,
            )
        if project.unsupported_instances:
            omitted = ", ".join(project.unsupported_instances)
            return SimulationResult(
                status=SimulationStatus.PARTIAL,
                reason=f"simulation ran without unsupported component(s): {omitted}",
                issues=all_issues,
                project=project,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                duration_ms=duration_ms,
                serial_text=serial_text,
                boot_marker_seen=boot_marker_seen,
                screenshot_verified=screenshot_verified,
            )
        return SimulationResult(
            status=SimulationStatus.PASSED,
            reason="wokwi-cli booted the firmware and captured a screenshot",
            issues=all_issues,
            project=project,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=duration_ms,
            serial_text=serial_text,
            boot_marker_seen=boot_marker_seen,
            screenshot_verified=screenshot_verified,
        )
    finally:
        _SIMULATION_SLOT.release()


def archive_project(project: WokwiProject, destination: str | Path) -> Path:
    """Zip the complete Wokwi project, excluding the archive itself."""
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(
        path
        for path in project.project_dir.rglob("*")
        if path.is_file() and path.resolve() != output.resolve()
    )
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(project.project_dir).as_posix())
    return output
