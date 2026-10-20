"""V1 membership: catalog ids exist, instance ids are unique, ESP32 exactly once."""

from __future__ import annotations

from sparkplan.models.buildspec import SupportedBuild
from sparkplan.models.catalog import BoardModel, Catalog
from sparkplan.models.spec import Issue
from sparkplan.validators.phase2.report import issue


def validate_membership(spec: SupportedBuild, catalog: Catalog, board: BoardModel) -> list[Issue]:
    issues: list[Issue] = []

    seen: dict[str, int] = {}
    for part in spec.parts:
        seen[part.instance_id] = seen.get(part.instance_id, 0) + 1

    for instance_id, count in seen.items():
        if count > 1:
            issues.append(
                issue(
                    "DUPLICATE_INSTANCE",
                    f"instance_id {instance_id!r} is used {count} times; "
                    f"instance ids must be unique.",
                    part_ids=[instance_id],
                    fix_hint="Give each part instance a distinct instance_id.",
                )
            )

    known_ids = {p.id for p in catalog.parts}
    for part in spec.parts:
        if part.part_id not in known_ids:
            issues.append(
                issue(
                    "UNKNOWN_PART",
                    f"part_id {part.part_id!r} is not in the catalog.",
                    part_ids=[part.instance_id],
                    fix_hint=f"Use a catalog part id, one of: {sorted(known_ids)}.",
                )
            )

    boards = spec.board_instances()
    if len(boards) == 0:
        issues.append(
            issue(
                "BOARD_MISSING",
                "the spec must contain the ESP32 board exactly once, but none is present.",
                fix_hint="Add one PartRef for 'esp32_devkit_30pin'.",
            )
        )
    elif len(boards) > 1:
        issues.append(
            issue(
                "BOARD_DUPLICATED",
                f"the spec contains the ESP32 board {len(boards)} times; exactly one is allowed.",
                part_ids=[b.instance_id for b in boards],
                fix_hint="Keep a single ESP32 board instance.",
            )
        )

    return issues
