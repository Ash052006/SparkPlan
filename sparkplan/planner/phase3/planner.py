"""Phase 3 planner: idea -> validated BuildSpec, or an explicit refusal.

Loop contract (see the approved plan):

* At most FOUR LLM calls per ``plan``: one initial call plus up to three
  repair calls (``MAX_REPAIR_ROUNDS = 3``).
* Transport retries (inside the client) are separate from planner repair
  rounds and are bounded to one per call.
* ``ok`` is returned ONLY when parsing yields a ``SupportedBuild`` AND the
  Phase 2 validator report contains no errors. Warnings pass through.
* Unsupported ideas return ``unsupported`` with the exact ``UnsupportedBuild``
  fields. Empty/whitespace input returns ``refused``.
* After the calls are exhausted with remaining errors, return ``failed`` with
  the final availability validation report and the attempt history. A
  half-valid spec is never labelled successful.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from sparkplan.llm.phase3.client import LLMClient, LLMError, LLMResponse
from sparkplan.models.buildspec import (
    SupportedBuild,
    UnsupportedBuild,
    parse_build_spec,
)
from sparkplan.models.catalog import Catalog, get_catalog
from sparkplan.models.spec import Issue
from sparkplan.planner.phase3.models import Attempt, PlanResult, PlanStatus
from sparkplan.planner.phase3.prompt import (
    build_system_prompt,
    build_user_message,
    log_request_size,
    provider_response_schema,
    unwrap_provider_response,
)
from sparkplan.validators.phase2.report import ValidationReport, run_all

MAX_REPAIR_ROUNDS = 3
MAX_LLM_CALLS = MAX_REPAIR_ROUNDS + 1


class Planner:
    """Idea -> validated BuildSpec using one LLM and the Phase 2 validators."""

    def __init__(self, llm: LLMClient, catalog: Catalog | None = None) -> None:
        self.llm = llm
        self.catalog = catalog if catalog is not None else get_catalog()
        self._system = build_system_prompt(self.catalog)
        self._schema = _response_schema()

    def plan(self, idea: str) -> PlanResult:
        raw = idea.strip()
        if not raw:
            return PlanResult(
                status=PlanStatus.REFUSED,
                idea=idea,
                reasons=["empty idea: provide a plain-language hardware description"],
                issues=[
                    Issue(
                        code="EMPTY_IDEA",
                        severity="error",
                        message="Idea is empty or whitespace-only.",
                        fix_hint="Describe the build you want, e.g. 'a plant moisture monitor'.",
                    )
                ],
            )

        attempts: list[Attempt] = []
        prompt_tokens = 0
        completion_tokens = 0
        estimated_request = 0
        last_report: ValidationReport | None = None
        last_issues: list[Issue] = []
        user = build_user_message(raw)
        llm_calls = 0

        for round_no in range(MAX_LLM_CALLS):
            llm_calls += 1
            estimated_request = log_request_size(self._system, user, self._schema)
            try:
                response = self.llm.complete_json(self._system, user, self._schema)
            except LLMError as exc:
                attempts.append(
                    Attempt(
                        round=round_no,
                        kind="transport_error",
                        detail=str(exc),
                        estimated_request_tokens=estimated_request,
                    )
                )
                return _failed(
                    idea,
                    attempts,
                    _transport_issues(exc),
                    prompt_tokens,
                    completion_tokens,
                    repair_rounds=max(0, round_no),
                    last_report=last_report,
                    estimated_request=estimated_request,
                )

            prompt_tokens += response.usage.prompt_tokens
            completion_tokens += response.usage.completion_tokens
            usage = response.usage

            # A ``finish_reason=length`` response is NOT rejected up-front: the
            # provider can truncate at the cap yet still emit complete, parseable
            # JSON. We always try to parse; truncation only changes how an
            # unparseable response is reported (and the repair hint).
            parsed, parse_issues = _parse(response)
            if parsed is None:
                if response.truncated:
                    kind = "truncated"
                    parse_issues = _truncation_issues()
                    detail = "finish_reason=length"
                else:
                    kind = "parse_error"
                    detail = "response did not satisfy the BuildSpec schema"
                last_issues = parse_issues
                attempts.append(
                    Attempt(
                        round=round_no,
                        kind=kind,
                        issues=parse_issues,
                        prompt_tokens=usage.prompt_tokens,
                        completion_tokens=usage.completion_tokens,
                        usage_present=usage.present,
                        detail=detail,
                        estimated_request_tokens=estimated_request,
                    )
                )
                if round_no >= MAX_REPAIR_ROUNDS:
                    break
                user = _repair_prompt(raw, None, parse_issues)
                continue

            if isinstance(parsed, UnsupportedBuild):
                attempts.append(
                    Attempt(
                        round=round_no,
                        kind="unsupported",
                        prompt_tokens=usage.prompt_tokens,
                        completion_tokens=usage.completion_tokens,
                        usage_present=usage.present,
                        detail="; ".join(parsed.reasons),
                        estimated_request_tokens=estimated_request,
                    )
                )
                return PlanResult(
                    status=PlanStatus.UNSUPPORTED,
                    idea=idea,
                    title=parsed.title,
                    reasons=list(parsed.reasons),
                    missing_capabilities=list(parsed.missing_capabilities),
                    closest_supported_alternative=parsed.closest_supported_alternative,
                    issues=_unsupported_issues(parsed),
                    attempts=attempts,
                    repair_rounds=max(0, round_no),
                    llm_calls=llm_calls,
                    total_prompt_tokens=prompt_tokens,
                    total_completion_tokens=completion_tokens,
                    estimated_request_tokens=estimated_request,
                )

            assert isinstance(parsed, SupportedBuild)
            report = run_all(parsed, self.catalog)
            last_report = report

            if report.passed:
                attempts.append(
                    Attempt(
                        round=round_no,
                        kind="ok",
                        issues=report.issues,
                        prompt_tokens=usage.prompt_tokens,
                        completion_tokens=usage.completion_tokens,
                        usage_present=usage.present,
                        estimated_request_tokens=estimated_request,
                    )
                )
                return PlanResult(
                    status=PlanStatus.OK,
                    idea=idea,
                    title=parsed.title,
                    spec=parsed,
                    report=report,
                    issues=report.issues,
                    attempts=attempts,
                    repair_rounds=max(0, round_no),
                    llm_calls=llm_calls,
                    total_prompt_tokens=prompt_tokens,
                    total_completion_tokens=completion_tokens,
                    estimated_request_tokens=estimated_request,
                )

            last_issues = report.errors
            attempts.append(
                Attempt(
                    round=round_no,
                    kind="validation_error",
                    issues=report.issues,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    usage_present=usage.present,
                    detail=f"{len(report.errors)} error(s)",
                    estimated_request_tokens=estimated_request,
                )
            )
            if round_no >= MAX_REPAIR_ROUNDS:
                break
            user = _repair_prompt(raw, parsed, report.errors)

        return _failed(
            idea,
            attempts,
            last_issues,
            prompt_tokens,
            completion_tokens,
            repair_rounds=MAX_REPAIR_ROUNDS,
            last_report=last_report,
            estimated_request=estimated_request,
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _response_schema() -> dict[str, Any]:
    # Send the union wrapped in a top-level object so strict providers accept
    # it; unwrap before parsing (see unwrap_provider_response).
    return provider_response_schema()


def _parse(response: LLMResponse) -> tuple[SupportedBuild | UnsupportedBuild | None, list[Issue]]:
    data = unwrap_provider_response(response.data)
    try:
        return parse_build_spec(data), []
    except ValidationError as exc:
        return None, [_parse_issue(exc)]
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        return None, [
            Issue(
                code="SPEC_PARSE_ERROR",
                severity="error",
                message=str(exc),
                fix_hint="Emit JSON matching the BuildSpec schema exactly.",
            )
        ]


def _parse_issue(exc: ValidationError) -> Issue:
    parts: list[str] = []
    msgs: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()))
        msgs.append(f"{loc}: {err.get('msg', '')}")
        if "part_id" in err.get("loc", ()):
            val = err.get("input")
            if isinstance(val, str):
                parts.append(val)
    return Issue(
        code="SPEC_PARSE_ERROR",
        severity="error",
        message="BuildSpec failed schema validation: " + "; ".join(msgs[:8]),
        part_ids=parts,
        fix_hint="Emit JSON matching the BuildSpec schema exactly.",
    )


def _transport_issues(exc: LLMError) -> list[Issue]:
    return [
        Issue(
            code="LLM_TRANSPORT",
            severity="error",
            message=str(exc),
            fix_hint="Check the LLM provider, base URL, key and model, then retry.",
        )
    ]


def _truncation_issues() -> list[Issue]:
    return [
        Issue(
            code="LLM_TRUNCATED",
            severity="error",
            message=(
                "The LLM response hit the completion token cap and was cut off, "
                "so the JSON is incomplete."
            ),
            fix_hint=(
                "Be terser: keep all strings short and emit the entire object "
                "(including layout and behaviors) within the budget."
            ),
        )
    ]


def _unsupported_issues(parsed: UnsupportedBuild) -> list[Issue]:
    return [
        Issue(
            code="UNSUPPORTED_IDEA",
            severity="error",
            message="; ".join(parsed.reasons),
            fix_hint=parsed.closest_supported_alternative,
        )
    ]


def _compact_candidate(candidate: SupportedBuild | None) -> str:
    """Serialize the current candidate spec as compact JSON (no empty optionals).

    Sent back on repair so the model AMENDS a known-good baseline instead of
    re-inventing the build (which caused unrequested parts and re-truncation).
    Empty optional strings/lists are dropped to keep the anchor small; only
    facts that could change a validator outcome are kept.
    """
    if candidate is None:
        return "null"
    data = candidate.model_dump()
    return json.dumps(_strip_empty(data), separators=(",", ":"), ensure_ascii=False)


def _strip_empty(value: Any) -> Any:
    """Recursively drop empty optional fields ("" / [] / None) from a dict/list."""
    if isinstance(value, dict):
        return {
            k: _strip_empty(v) for k, v in value.items() if not (v is None or v == "" or v == [])
        }
    if isinstance(value, list):
        return [_strip_empty(v) for v in value]
    return value


def _repair_prompt(idea: str, candidate: SupportedBuild | None, issues: list[Issue]) -> str:
    """Actionable repair feedback: the idea, the current candidate, the errors.

    The candidate spec is echoed (compact) so the model can correct only what is
    broken while preserving everything already valid. Only error-severity issues
    are sent. Explicit instructions forbid adding unrequested parts and require
    every pin connection and one layout entry per non-board part.
    """
    machine = json.dumps(
        [
            {
                "code": i.code,
                "severity": i.severity,
                "message": i.message,
                "part_ids": i.part_ids,
                "fix_hint": i.fix_hint,
            }
            for i in issues
            if i.severity == "error"
        ],
        separators=(",", ":"),
    )
    return (
        f"{build_user_message(idea)}\n\n"
        "Your PREVIOUS BuildSpec (compact JSON) is below. Keep every part, "
        "connection and layout entry that is already valid; change ONLY what the "
        "errors require. Do NOT add any component the user did not ask for - never "
        "introduce a part merely to silence an unrelated error - and do NOT drop a "
        "part that is still needed.\n"
        f"CURRENT_SPEC: {_compact_candidate(candidate)}\n\n"
        f"ERRORS TO FIX (all are blocking; fix every one):\n{machine}\n\n"
        "CHECK before replying: (a) every non-board part has VCC -> a rail and "
        "GND -> a board GND; (b) EVERY remaining pin of every non-board part is an "
        "endpoint of some connection; (c) there is exactly one layout entry "
        "{{part_id, mount}} for every non-board part.\n\n"
        'Return the FULL corrected BuildSpec inside {"build": <BuildSpec>} JSON only.'
    )


def _failed(
    idea: str,
    attempts: list[Attempt],
    issues: list[Issue],
    prompt_tokens: int,
    completion_tokens: int,
    *,
    repair_rounds: int,
    last_report: ValidationReport | None,
    estimated_request: int = 0,
) -> PlanResult:
    reasons = [
        f"BuildSpec still invalid after {repair_rounds} repair round(s); "
        "no valid spec was produced.",
    ]
    reasons += [f"{i.code}: {i.message}" for i in issues if i.severity == "error"][:10]
    if not issues:
        reasons.append("No LLM response could be obtained.")
    return PlanResult(
        status=PlanStatus.FAILED,
        idea=idea,
        reasons=reasons,
        report=last_report,
        issues=issues,
        attempts=attempts,
        repair_rounds=repair_rounds,
        llm_calls=len([a for a in attempts]),
        total_prompt_tokens=prompt_tokens,
        total_completion_tokens=completion_tokens,
        estimated_request_tokens=estimated_request,
    )
