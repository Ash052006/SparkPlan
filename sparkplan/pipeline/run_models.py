"""Typed run results for the Phase 6 pipeline.

``StageStatus`` describes what happened in one pipeline stage. ``RunStatus``
describes the whole run. They are deliberately separate: a run can finish
``ok`` while a verification stage is honestly ``skipped`` or ``unverified``,
but a skipped or unverified stage is never represented as ``passed``. A run
with any failed stage must have the overall status ``failed``. Refused and
unsupported runs are terminal non-success outcomes and may carry a failed
plan stage without being mislabelled as a generic failure.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sparkplan.models.spec import Issue

MAX_RUN_ID_LENGTH = 64
RUN_ID_PATTERN = r"^[A-Za-z0-9_-]+$"

_SAFE_REFERENCE_KEY = re.compile(r"^[A-Za-z0-9_.-]+$")
_SAFE_REFERENCE_MAX_LENGTH = 240


class RunStatus(str, Enum):
    """Overall lifecycle state of one run."""

    RUNNING = "running"
    OK = "ok"
    REFUSED = "refused"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class StageStatus(str, Enum):
    """Truthful outcome of one pipeline stage."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNVERIFIED = "unverified"


def _safe_references(values: dict[str, str]) -> dict[str, str]:
    """Validate and deterministically order artifact/evidence references."""
    for key, value in values.items():
        if not key or len(key) > 80 or not _SAFE_REFERENCE_KEY.fullmatch(key):
            raise ValueError(f"unsafe reference key: {key!r}")
        if key in {".", ".."}:
            raise ValueError(f"unsafe reference key: {key!r}")
        if not value or len(value) > _SAFE_REFERENCE_MAX_LENGTH:
            raise ValueError(f"unsafe reference for {key!r}: {value!r}")
        if (
            value.startswith("/")
            or "\\" in value
            or ":" in value
            or "~" in value
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError(f"reference must be a relative POSIX path: {value!r}")
        parts = value.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError(f"reference must be a relative POSIX path: {value!r}")
    return dict(sorted(values.items()))


class RunRefusal(BaseModel):
    """Planner-provided refusal details for refused or unsupported ideas."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reasons: list[str] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)
    closest_supported_alternative: str = ""


class RunStageResult(BaseModel):
    """Outcome and safe references for one stage of a run.

    ``skipped`` and ``unverified`` require a reason. A ``passed`` stage cannot
    carry error-severity issues. Artifact and evidence references are relative
    paths only; no absolute paths or object internals are serialized.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    status: StageStatus
    started_at: str | None = Field(default=None, max_length=64)
    finished_at: str | None = Field(default=None, max_length=64)
    duration_ms: float | None = Field(default=None, ge=0.0)
    issues: list[Issue] = Field(default_factory=list)
    reason: str | None = Field(default=None, max_length=2000)
    evidence: dict[str, str] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)

    @field_validator("evidence", "artifacts")
    @classmethod
    def _validate_references(cls, value: dict[str, str]) -> dict[str, str]:
        return _safe_references(value)

    @model_validator(mode="after")
    def _honest_status(self) -> RunStageResult:
        if self.status in {StageStatus.SKIPPED, StageStatus.UNVERIFIED} and (
            self.reason is None or not self.reason.strip()
        ):
            raise ValueError(f"{self.status.value} stage {self.name!r} requires a reason")
        if self.status is StageStatus.PASSED and any(
            issue.severity == "error" for issue in self.issues
        ):
            raise ValueError(f"passed stage {self.name!r} cannot contain error issues")
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
            "issues": [issue.model_dump(mode="json") for issue in self.issues],
            "reason": self.reason,
            "evidence": dict(sorted(self.evidence.items())),
            "artifacts": dict(sorted(self.artifacts.items())),
        }


class RunResult(BaseModel):
    """Serializable outcome of one run and its artifact inventory.

    The overall status is ``ok`` only when no stage failed. Requested checks
    that could not run remain ``skipped`` or ``unverified`` and are visible in
    their stage results. Report metadata is explicit; no environment or
    arbitrary object data is serialized.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    run_id: str = Field(
        min_length=1,
        max_length=MAX_RUN_ID_LENGTH,
        pattern=RUN_ID_PATTERN,
    )
    status: RunStatus
    stages: list[RunStageResult] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(default_factory=dict)
    refusal: RunRefusal | None = None
    created_at: str | None = Field(default=None, max_length=64)
    updated_at: str | None = Field(default=None, max_length=64)
    finished_at: str | None = Field(default=None, max_length=64)

    @field_validator("artifacts")
    @classmethod
    def _validate_artifact_inventory(cls, value: dict[str, str]) -> dict[str, str]:
        return _safe_references(value)

    @model_validator(mode="after")
    def _consistent_status(self) -> RunResult:
        names = [stage.name for stage in self.stages]
        if len(names) != len(set(names)):
            raise ValueError("run stage names must be unique")
        failed = [stage.name for stage in self.stages if stage.status is StageStatus.FAILED]
        if failed and self.status not in {
            RunStatus.FAILED,
            RunStatus.REFUSED,
            RunStatus.UNSUPPORTED,
        }:
            raise ValueError(f"run status must be failed when stages failed: {', '.join(failed)}")
        if self.status in {RunStatus.REFUSED, RunStatus.UNSUPPORTED} and set(failed) - {"plan"}:
            raise ValueError("refused or unsupported runs may only fail the plan stage")
        return self

    @property
    def failed_stages(self) -> list[RunStageResult]:
        return [stage for stage in self.stages if stage.status is StageStatus.FAILED]

    def stage(self, name: str) -> RunStageResult | None:
        for stage in self.stages:
            if stage.name == name:
                return stage
        return None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "status": self.status.value,
            "stages": [stage.to_dict() for stage in self.stages],
            "issues": [issue.model_dump(mode="json") for issue in self.issues],
            "artifacts": dict(sorted(self.artifacts.items())),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
        }
        if self.refusal is not None:
            payload["refusal"] = self.refusal.model_dump(mode="json")
        return payload

    def to_json(self) -> str:
        return (
            json.dumps(
                self.to_dict(),
                sort_keys=True,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        )
