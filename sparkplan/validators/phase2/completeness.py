"""V7 completeness: every required pin of every part is connected.

Every non-board part pin must appear in at least one connection (from either
endpoint). The board is the hub, so its pins are not required to be connected.
"""

from __future__ import annotations

from sparkplan.models.buildspec import SupportedBuild
from sparkplan.models.catalog import BoardModel, Catalog
from sparkplan.models.spec import Issue
from sparkplan.validators.phase2.report import issue


def validate_completeness(spec: SupportedBuild, catalog: Catalog, board: BoardModel) -> list[Issue]:
    issues: list[Issue] = []

    connected: set[tuple[str, str]] = set()
    for conn in spec.connections:
        connected.add((conn.from_instance, conn.from_pin))
        connected.add((conn.to_instance, conn.to_pin))

    for part_ref in spec.parts:
        part = catalog.get_or_none(part_ref.part_id)
        if part is None or part.category.value == "board":
            continue
        missing = [p.name for p in part.pins if (part_ref.instance_id, p.name) not in connected]
        if missing:
            issues.append(
                issue(
                    "PIN_UNCONNECTED",
                    f"{part_ref.instance_id} ({part.id}) has unconnected pins: {missing}.",
                    part_ids=[part_ref.instance_id],
                    fix_hint="Connect every required pin (VCC, GND and signals).",
                )
            )

    return issues
