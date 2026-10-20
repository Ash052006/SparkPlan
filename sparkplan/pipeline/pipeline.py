"""Pipeline orchestration: plan -> validate -> geometry -> firmware -> guide.

Each stage lives in its own package and is callable on its own; the
pipeline only sequences them and converts stage failures into Issues.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sparkplan.catalog import Catalog, load_catalog
from sparkplan.firmware import FirmwareError, FirmwareResult, compile_firmware, generate_firmware
from sparkplan.geometry import (
    EnclosureMeshes,
    EnclosurePlan,
    GeometryEngineError,
    build_enclosure,
    compute_enclosure_plan,
)
from sparkplan.guide import render_guide
from sparkplan.llm.client import LLMClient
from sparkplan.models.spec import BuildSpec, Issue, PlanResult, PlanStatus
from sparkplan.planner import Planner


@dataclass
class PipelineResult:
    status: Literal["ok", "refused", "failed"]
    idea: str
    plan: PlanResult | None = None
    spec: BuildSpec | None = None
    enclosure_plan: EnclosurePlan | None = None
    enclosure: EnclosureMeshes | None = None
    firmware: FirmwareResult | None = None
    guide: str | None = None
    issues: list[Issue] = field(default_factory=list)

    @property
    def refusal_reasons(self) -> list[str]:
        if self.plan and self.plan.refusal:
            return self.plan.refusal.reasons
        return []


def run_pipeline(
    idea: str,
    llm: LLMClient,
    catalog: Catalog | None = None,
    out_dir: str | Path | None = None,
    do_compile: bool = False,
) -> PipelineResult:
    catalog = catalog or load_catalog()
    planner = Planner(llm, catalog)
    plan = planner.plan(idea)

    if plan.status is not PlanStatus.OK or plan.spec is None:
        status = "refused" if plan.status is PlanStatus.REFUSED else "failed"
        return PipelineResult(status=status, idea=idea, plan=plan, issues=list(plan.issues))

    spec = plan.spec
    issues = list(plan.issues)

    try:
        enclosure_plan = compute_enclosure_plan(spec, catalog)
        enclosure = build_enclosure(enclosure_plan)
    except GeometryEngineError as exc:
        issues.append(Issue(code="GEOMETRY_FAILED", severity="error", message=str(exc)))
        return PipelineResult(status="failed", idea=idea, plan=plan, spec=spec, issues=issues)

    try:
        firmware = generate_firmware(spec, catalog)
        if do_compile:
            firmware = compile_firmware(firmware, Path(out_dir or ".") / "sparkplan_ino")
    except FirmwareError as exc:
        issues.append(Issue(code="FIRMWARE_FAILED", severity="error", message=str(exc)))
        return PipelineResult(
            status="failed",
            idea=idea,
            plan=plan,
            spec=spec,
            enclosure_plan=enclosure_plan,
            enclosure=enclosure,
            issues=issues,
        )

    guide = render_guide(spec, catalog, enclosure_plan, firmware, issues)

    if out_dir is not None:
        out = Path(out_dir)
        enclosure.export_base_stl(out / "enclosure_base.stl")
        enclosure.export_lid_stl(out / "enclosure_lid.stl")
        (out / "sparkplan_ino").mkdir(parents=True, exist_ok=True)
        (out / "sparkplan_ino" / "sparkplan.ino").write_text(firmware.sketch)
        (out / "BUILD_GUIDE.md").write_text(guide)
        (out / "buildspec.json").write_text(spec.model_dump_json(indent=2))

    return PipelineResult(
        status="ok",
        idea=idea,
        plan=plan,
        spec=spec,
        enclosure_plan=enclosure_plan,
        enclosure=enclosure,
        firmware=firmware,
        guide=guide,
        issues=issues,
    )
