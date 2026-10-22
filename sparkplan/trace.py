"""Per-run trace records for SparkPlan pipeline executions.

A trace is an additive artifact: it records what actually happened during a
run without changing the pipeline's return value.  Stages that were not
attempted are recorded as ``skipped`` or ``unverified``; they are never
reported as ``passed``.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sparkplan.models.spec import Issue

StageStatus = Literal["passed", "failed", "skipped", "unverified"]
RunStatus = Literal["running", "passed", "failed"]

PIPELINE_STAGES: tuple[str, ...] = (
    "plan",
    "validation",
    "geometry_layout",
    "geometry_meshes",
    "geometry_stl_export",
    "firmware_generation",
    "firmware_compile",
    "guide",
    "slicing",
    "simulation",
)
_SIMULATION_REASON = "Wokwi or hardware simulation was not performed"


def complete_trace_stages(trace: RunTrace, *, reason: str) -> None:
    """Add every canonical stage that has not already been recorded.

    Existing stages are never changed. Unattempted stages become ``skipped``;
    simulation is the exception because it is always recorded as
    ``unverified`` rather than implying that a simulation passed.
    """
    for stage in PIPELINE_STAGES:
        if stage in trace.stages:
            continue
        if stage == "simulation":
            trace.record_stage(stage, "unverified", reason=_SIMULATION_REASON)
        else:
            trace.record_stage(stage, "skipped", reason=reason)


def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class TraceStage:
    """One stage in a run trace."""

    name: str
    status: StageStatus
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: float | None = None
    issues: list[Issue] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    reason: str | None = None


@dataclass
class RunTrace:
    """The complete trace for one pipeline run."""

    schema_version: str
    run_id: str
    status: RunStatus
    started_at: str
    finished_at: str | None = None
    idea: str | None = None
    stages: dict[str, TraceStage] = field(default_factory=dict)
    tool_versions: dict[str, str | None] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    trace_write_error: str | None = None

    def record_stage(
        self,
        name: str,
        status: StageStatus,
        *,
        started_at: str | None = None,
        finished_at: str | None = None,
        duration_ms: float | None = None,
        issues: list[Issue] | None = None,
        details: dict[str, Any] | None = None,
        artifacts: dict[str, str] | None = None,
        reason: str | None = None,
    ) -> None:
        stage = TraceStage(
            name=name,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            issues=list(issues or []),
            details=dict(details or {}),
            artifacts=dict(artifacts or {}),
            reason=reason,
        )
        self.stages[name] = stage

    def finalize(self, status: RunStatus, *, finished_at: str | None = None) -> None:
        self.status = status
        self.finished_at = finished_at or utc_now()

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


def _jsonable(value: Any) -> Any:
    """Convert a value to JSON-compatible data without hiding type errors."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Issue):
        return value.model_dump(mode="json")
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime,)):
        return value.isoformat().replace("+00:00", "Z")
    from enum import Enum

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (RunTrace, TraceStage)):
        return _jsonable(asdict(value))
    raise TypeError(f"value of type {type(value).__name__} is not JSON serializable")


def new_run_id() -> str:
    return uuid.uuid4().hex


def create_trace(run_id: str, idea: str, started_at: str) -> RunTrace:
    return RunTrace(
        schema_version="1.0",
        run_id=run_id,
        status="running",
        started_at=started_at,
        idea=idea,
    )


def write_trace(trace: RunTrace, path: Path) -> None:
    """Write ``trace`` to ``path`` atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{trace.run_id}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(
                trace.to_dict(),
                handle,
                sort_keys=True,
                indent=2,
                ensure_ascii=False,
                default=_jsonable,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        with contextlib.suppress(OSError):
            temp_path.unlink(missing_ok=True)
        raise


def claim_trace_path(path: Path | str) -> Path:
    """Atomically claim ``path`` for a trace.

    The path is created exclusively.  If it already exists, or another
    process claims it first, ``FileExistsError`` is raised and the existing
    file is left untouched.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except OSError as exc:
        if exc.errno in (errno.EEXIST, errno.EISDIR):
            raise FileExistsError(f"trace path already exists: {path}") from exc
        raise
    os.close(descriptor)
    return path
