"""Planner: idea -> validated BuildSpec via LLM with deterministic repair.

The LLM never writes geometry or firmware. It emits one JSON object
(BuildSpec, or an explicit refusal) which deterministic validators check.
Failures are fed back as machine-readable issues, at most
MAX_REPAIR_ROUNDS times. Unsupported ideas are refused with reasons;
they are never approximated with "close enough" catalog parts.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from sparkplan.catalog import Catalog, load_catalog
from sparkplan.llm.client import LLMClient, LLMError
from sparkplan.models.spec import BuildSpec, Issue, PlanResult, PlanStatus, Refusal
from sparkplan.validators import has_errors, issues_to_machine, run_validators

MAX_REPAIR_ROUNDS = 3

SYSTEM_PROMPT = """\
You are SparkPlan, a hardware build planner. You convert one plain-language
idea into a BuildSpec JSON object using ONLY the parts in the catalog below.

HARD RULES:
1. You may only reference part_ids, pins, and api names that appear in the
   catalog digest. Never invent parts. Never substitute a similar part.
2. If the idea cannot be built exactly from this catalog, reply with the
   refusal shape {"supported": false, "reasons": ["..."], "detected_capabilities": [...]}.
   Refuse; do NOT approximate.
3. Every peripheral signal pin (not power/ground) must have a connection to
   a free, capable ESP32 GPIO. Respect the GPIO table: input-only pins cannot
   drive outputs; analog pins need ADC; I2C parts share GPIO21 (SDA) and
   GPIO22 (SCL); SPI parts share GPIO18/23/19 with a private CS each.
4. Stay inside power budgets: the 3v3 rail has 500 mA; the USB 5V rail has
   500 mA including ~250 mA for the ESP32. A servo or NeoPixel ring exceeds
   that: add battery_holder_18650 + mt3608_boost to unlock a 1 A 5V supply.
5. Every functional part needs at least one action using a catalog api name.
   write actions may consume the action_id of a read action.
6. instance_ids are short snake_case names (e.g. "env", "screen", "arm").

Reply with ONLY the JSON object.
"""

REFUSAL_SHAPE = {
    "supported": False,
    "reasons": ["why the idea is out of catalog scope"],
    "detected_capabilities": ["capability ids if any matched"],
}


def build_specs_schema() -> dict[str, Any]:
    spec_schema = BuildSpec.model_json_schema()
    refusal_schema = {
        "type": "object",
        "properties": {
            "supported": {"type": "boolean", "const": False},
            "reasons": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "detected_capabilities": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["supported", "reasons"],
        "additionalProperties": False,
    }
    return {"anyOf": [refusal_schema, spec_schema]}


def catalog_digest(catalog: Catalog) -> str:
    """Grounding text: the full catalog in compact JSON."""
    parts = []
    for part in catalog.parts.values():
        parts.append(
            {
                "part_id": part.part_id,
                "category": part.category.value,
                "rail": part.supply.rail,
                "voltage": [part.supply.voltage_min, part.supply.voltage_max],
                "i_ma_max": part.supply.i_ma_max,
                "pins": [{"name": p.name, "kind": p.kind} for p in part.pins],
                "api": [
                    {"name": a.name, "kind": a.kind, "param": a.param} for a in part.firmware.api
                ],
                "power_role": part.power_role.model_dump() if part.power_role else None,
            }
        )
    payload = {
        "controller": catalog.power.controller,
        "parts": parts,
        "gpio_table": {k: v.model_dump() for k, v in catalog.gpios.gpios.items()},
        "gpio_defaults": catalog.gpios.defaults,
        "rails": {k: v.model_dump() for k, v in catalog.power.rails.items()},
        "capabilities": [
            {"id": c.id, "keywords": c.keywords[:8], "parts": c.parts}
            for c in catalog.capabilities.capabilities
        ],
    }
    return json.dumps(payload, indent=1)


class Planner:
    def __init__(self, llm: LLMClient, catalog: Catalog | None = None) -> None:
        self.llm = llm
        self.catalog = catalog or load_catalog()
        self._digest = catalog_digest(self.catalog)

    def plan(self, idea: str) -> PlanResult:
        idea = idea.strip()
        if not idea:
            return PlanResult(
                status=PlanStatus.REFUSED,
                refusal=Refusal(reasons=["empty idea"]),
                issues=[Issue(code="EMPTY_IDEA", severity="error", message="Idea is empty.")],
            )

        # Deterministic pre-filter: ideas that need out-of-catalog parts are
        # refused without even consulting the LLM.
        refusal_kws = self.catalog.capabilities.refusals_for(idea)
        if refusal_kws:
            note = self.catalog.capabilities.refusal_note
            return PlanResult(
                status=PlanStatus.REFUSED,
                refusal=Refusal(
                    reasons=[
                        f"Idea mentions {kw!r}, which is outside the SparkPlan catalog."
                        for kw in refusal_kws
                    ]
                    + [note],
                    detected_capabilities=[],
                ),
                issues=[
                    Issue(
                        code="UNSUPPORTED_IDEA",
                        severity="error",
                        message=f"refused before planning: matched refusal keywords {refusal_kws}",
                        fix_hint=note,
                    )
                ],
            )

        user = f"IDEA:\n{idea}\n\nCATALOG DIGEST:\n{self._digest}\n\nBUILD SPEC JSON:"
        schema = build_specs_schema()
        current_user = user
        all_issues: list[Issue] = []
        last_payload: dict[str, Any] | None = None

        for attempt in range(MAX_REPAIR_ROUNDS + 1):
            try:
                payload = self.llm.complete_json(SYSTEM_PROMPT, current_user, schema)
            except LLMError as exc:
                all_issues.append(Issue(code="LLM_TRANSPORT", severity="error", message=str(exc)))
                return PlanResult(
                    status=PlanStatus.EXHAUSTED,
                    refusal=Refusal(reasons=[f"LLM unavailable: {exc}"]),
                    issues=all_issues,
                    repair_rounds=attempt,
                )
            last_payload = payload

            if isinstance(payload, dict) and payload.get("supported") is False:
                reasons = [str(r) for r in payload.get("reasons") or ["unsupported"]]
                detected = [str(c) for c in payload.get("detected_capabilities") or []]
                return PlanResult(
                    status=PlanStatus.REFUSED,
                    refusal=Refusal(reasons=reasons, detected_capabilities=detected),
                    issues=all_issues
                    + [
                        Issue(
                            code="UNSUPPORTED_IDEA",
                            severity="error",
                            message="; ".join(reasons),
                            fix_hint=self.catalog.capabilities.refusal_note,
                        )
                    ],
                    repair_rounds=attempt,
                )

            try:
                spec = BuildSpec.model_validate(payload)
            except ValidationError as exc:
                all_issues.append(_parse_issue(exc))
                if attempt >= MAX_REPAIR_ROUNDS:
                    break
                current_user = _repair_prompt(idea, last_payload, all_issues, self._digest)
                continue

            issues = run_validators(spec, self.catalog)
            if not has_errors(issues):
                return PlanResult(
                    status=PlanStatus.OK,
                    spec=spec,
                    issues=issues,
                    repair_rounds=attempt,
                )

            all_issues = list(issues)
            if attempt >= MAX_REPAIR_ROUNDS:
                break
            current_user = _repair_prompt(idea, last_payload, all_issues, self._digest)

        return PlanResult(
            status=PlanStatus.EXHAUSTED,
            refusal=Refusal(
                reasons=[
                    f"BuildSpec still invalid after {MAX_REPAIR_ROUNDS} repair rounds.",
                    *[f"{i.code}: {i.message}" for i in all_issues if i.severity == "error"][:10],
                ]
            ),
            issues=all_issues,
            repair_rounds=MAX_REPAIR_ROUNDS,
        )


def _parse_issue(exc: ValidationError) -> Issue:
    parts = []
    msgs = []
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


def _repair_prompt(
    idea: str, payload: dict[str, Any] | None, issues: list[Issue], digest: str
) -> str:
    machine = json.dumps(issues_to_machine(issues), indent=1)
    return (
        f"IDEA:\n{idea}\n\n"
        f"CATALOG DIGEST:\n{digest}\n\n"
        f"YOUR PREVIOUS REPLY (invalid):\n{json.dumps(payload, indent=1)}\n\n"
        f"DETERMINISTIC VALIDATOR ERRORS (fix every error, heed warnings):\n{machine}\n\n"
        "Reply with the corrected BuildSpec JSON only."
    )
