"""Phase 3 planner result models.

``PlanResult`` carries the final BuildSpec (or the reason there is none),
the Phase 2 validation report, the full attempt history and the accumulated
token totals. It is deliberately separate from the legacy
``sparkplan.models.spec.PlanResult``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from sparkplan.models.buildspec import SupportedBuild
from sparkplan.models.spec import Issue
from sparkplan.validators.phase2.report import ValidationReport


class PlanStatus(str, Enum):
    """The four terminal outcomes of a planning attempt.

    * ``OK``          - a SupportedBuild that passed Phase 2 with no errors.
    * ``UNSUPPORTED`` - the LLM declared the idea out of catalog scope.
    * ``FAILED``      - unrecoverable: parse/validation/transport exhausted.
    * ``REFUSED``     - empty/whitespace-only input (the only refused case).
    """

    OK = "ok"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    REFUSED = "refused"


@dataclass
class Attempt:
    """One LLM call and what the planner did with its response."""

    round: int
    kind: str  # parse_error | validation_error | transport_error | unsupported | ok
    issues: list[Issue] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usage_present: bool = False
    detail: str = ""


@dataclass
class PlanResult:
    """The outcome of ``Planner.plan`` including history and token totals."""

    status: PlanStatus
    idea: str
    title: str = ""
    spec: SupportedBuild | None = None
    reasons: list[str] = field(default_factory=list)
    missing_capabilities: list[str] = field(default_factory=list)
    closest_supported_alternative: str = ""
    report: ValidationReport | None = None
    issues: list[Issue] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    repair_rounds: int = 0
    llm_calls: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    estimated_request_tokens: int = 0

    @property
    def ok(self) -> bool:
        return self.status is PlanStatus.OK

    @property
    def passed(self) -> bool:
        """True only for a validated, error-free supported build."""
        return self.status is PlanStatus.OK and self.spec is not None

    def error_issues(self) -> list[Issue]:
        if self.report is not None:
            return self.report.errors
        return [i for i in self.issues if i.severity == "error"]
