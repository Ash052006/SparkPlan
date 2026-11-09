"""Structured outcomes for deterministic Wokwi simulations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from sparkplan.models.spec import Issue


class SimulationStatus(str, Enum):
    """Truthful outcome of one Wokwi simulation attempt."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    PARTIAL = "partial"


@dataclass(frozen=True)
class WokwiProject:
    """A generated Wokwi project and the parts it could not represent."""

    project_dir: Path
    diagram_path: Path
    config_path: Path
    scenario_path: Path
    serial_log_path: Path
    screenshot_path: Path
    board_instance_id: str | None
    screenshot_part_id: str | None
    unsupported_instances: list[str] = field(default_factory=list)
    unsupported_part_ids: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.unsupported_instances and not self.error_issues

    @property
    def error_issues(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "error"]


@dataclass(frozen=True)
class SimulationResult:
    """The result of running a generated project through wokwi-cli."""

    status: SimulationStatus
    reason: str
    issues: list[Issue] = field(default_factory=list)
    project: WokwiProject | None = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_ms: float | None = None
    serial_text: str = ""
    boot_marker_seen: bool = False
    screenshot_verified: bool = False
    timed_out: bool = False

    @property
    def error_issues(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == "error"]
