"""V8 behaviors: rules and display lines reference real readings/actuators.

A *reading* is either declared on a Behavior (name + unit) or provided by a
catalog part in the spec (from the part's firmware readings). An *actuator* is a
part instance in the spec whose catalog part has an actuator capability.

Every rule and display line must reference an existing reading or actuator, and
the unit it declares must match the reading's unit.
"""

from __future__ import annotations

from sparkplan.models.buildspec import SupportedBuild
from sparkplan.models.catalog import BoardModel, Catalog
from sparkplan.models.spec import Issue
from sparkplan.validators.phase2.report import issue


def _known_readings(spec: SupportedBuild, catalog: Catalog) -> dict[str, str]:
    """name -> unit for every reading available in this spec."""
    readings: dict[str, str] = {}
    for behavior in spec.behaviors:
        for r in behavior.readings:
            readings[r.name] = r.unit
    for part_ref in spec.parts:
        part = catalog.get_or_none(part_ref.part_id)
        if part is None:
            continue
        for r in part.firmware.readings:
            readings.setdefault(r.name, r.unit)
    return readings


def _known_actuators(spec: SupportedBuild, catalog: Catalog) -> set[str]:
    actuators: set[str] = set()
    for part_ref in spec.parts:
        part = catalog.get_or_none(part_ref.part_id)
        if part is None:
            continue
        if part.category.value == "actuator":
            actuators.add(part_ref.instance_id)
            actuators.add(part.id)
    return actuators


def validate_behaviors(spec: SupportedBuild, catalog: Catalog, board: BoardModel) -> list[Issue]:
    issues: list[Issue] = []
    readings = _known_readings(spec, catalog)
    actuators = _known_actuators(spec, catalog)

    for behavior in spec.behaviors:
        for rule in behavior.rules:
            if rule.condition_reading not in readings:
                issues.append(
                    issue(
                        "BEHAVIOR_UNKNOWN_READING",
                        f"behavior {behavior.name!r} rule {rule.name!r} references "
                        f"unknown reading {rule.condition_reading!r}.",
                        fix_hint=f"Use one of the readings: {sorted(readings)}.",
                    )
                )
            elif rule.condition_unit and rule.condition_unit != readings[rule.condition_reading]:
                issues.append(
                    issue(
                        "BEHAVIOR_UNIT_MISMATCH",
                        f"rule {rule.name!r} declares unit {rule.condition_unit!r} but "
                        f"reading {rule.condition_reading!r} is in "
                        f"{readings[rule.condition_reading]!r}.",
                        fix_hint="Make the rule's unit match the reading's unit.",
                    )
                )

            if rule.action_reading and rule.action_reading not in readings:
                issues.append(
                    issue(
                        "BEHAVIOR_UNKNOWN_READING",
                        f"rule {rule.name!r} acts on unknown reading " f"{rule.action_reading!r}.",
                        fix_hint=f"Use one of the readings: {sorted(readings)}.",
                    )
                )
            if rule.action_actuator and rule.action_actuator not in actuators:
                issues.append(
                    issue(
                        "BEHAVIOR_UNKNOWN_ACTUATOR",
                        f"rule {rule.name!r} acts on unknown actuator "
                        f"{rule.action_actuator!r}.",
                        fix_hint=f"Use one of the actuators: {sorted(actuators)}.",
                    )
                )
            if not rule.action_reading and not rule.action_actuator:
                issues.append(
                    issue(
                        "BEHAVIOR_NO_ACTION",
                        f"rule {rule.name!r} has no action_reading or action_actuator.",
                        fix_hint="Give the rule something to do.",
                    )
                )

        for line in behavior.display_lines:
            if line.reading not in readings:
                issues.append(
                    issue(
                        "BEHAVIOR_UNKNOWN_READING",
                        f"display line on {line.target_instance!r} references unknown "
                        f"reading {line.reading!r}.",
                        part_ids=[line.target_instance],
                        fix_hint=f"Use one of the readings: {sorted(readings)}.",
                    )
                )
            elif line.unit and line.unit != readings[line.reading]:
                issues.append(
                    issue(
                        "BEHAVIOR_UNIT_MISMATCH",
                        f"display line declares unit {line.unit!r} but reading "
                        f"{line.reading!r} is in {readings[line.reading]!r}.",
                        part_ids=[line.target_instance],
                        fix_hint="Make the display line's unit match the reading's unit.",
                    )
                )

    return issues
