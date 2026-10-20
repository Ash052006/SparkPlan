"""V9 layout: every part has a mount zone; wall/lid parts have an exposure.

Every non-board part must have an explicit mount zone (a LayoutHint). Parts
mounted on the wall or the lid must additionally declare an exposure (a face and
opening), taken from the catalog part, so the enclosure stage knows where to
cut.
"""

from __future__ import annotations

from sparkplan.models.buildspec import SupportedBuild
from sparkplan.models.catalog import BoardModel, Catalog, ExposureFace, Mount
from sparkplan.models.spec import Issue
from sparkplan.validators.phase2.report import issue


def validate_layout(spec: SupportedBuild, catalog: Catalog, board: BoardModel) -> list[Issue]:
    issues: list[Issue] = []

    # instance_id -> mount, from LayoutHints (hints are per part_id)
    hint_for_part = {h.part_id: h for h in spec.layout}

    for part_ref in spec.parts:
        part = catalog.get_or_none(part_ref.part_id)
        if part is None or part.category.value == "board":
            continue

        hint = hint_for_part.get(part_ref.part_id)

        if hint is None:
            issues.append(
                issue(
                    "NO_MOUNT_ZONE",
                    f"{part_ref.instance_id} ({part.id}) has no mount zone.",
                    part_ids=[part_ref.instance_id],
                    fix_hint="Add a LayoutHint with mount base, lid or wall.",
                )
            )
            continue

        if (
            hint.mount in {Mount.WALL.value, Mount.LID.value}
            and part.exposure.face is ExposureFace.NONE
        ):
            issues.append(
                issue(
                    "NO_EXPOSURE",
                    f"{part_ref.instance_id} ({part.id}) is mounted on the {hint.mount} "
                    f"but declares no exposure face.",
                    part_ids=[part_ref.instance_id],
                    fix_hint="Declare an exposure face so the enclosure has an opening.",
                )
            )

    return issues
