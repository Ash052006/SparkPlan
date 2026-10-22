"""V8 behaviors: rules and display lines reference real readings/actuators.

A *reading* is provided by a catalog part in the spec (from the part's firmware
readings). A Behavior reading must bind to the instance that produces it and use
that part's exact reading name and unit. An *actuator* is a part instance in the
spec whose catalog part has an actuator capability.

Every rule and display line must reference an existing reading or actuator, and
the unit it declares must match the reading's unit.
"""

from __future__ import annotations

from sparkplan.models.buildspec import Behavior, SupportedBuild
from sparkplan.models.catalog import BoardModel, Catalog
from sparkplan.models.spec import Issue
from sparkplan.validators.phase2.report import issue


def _known_readings(spec: SupportedBuild, catalog: Catalog) -> dict[str, str]:
    """name -> unit for every catalog reading available in this spec."""
    readings: dict[str, str] = {}
    for part_ref in spec.parts:
        part = catalog.get_or_none(part_ref.part_id)
        if part is None:
            continue
        for r in part.firmware.readings:
            readings.setdefault(r.name, r.unit)
    return readings


def _reading_issues(
    spec: SupportedBuild, catalog: Catalog, behavior: Behavior, instances: list[str]
) -> list[Issue]:
    issues: list[Issue] = []
    for reading in behavior.readings:
        if not reading.instance_id:
            issues.append(
                issue(
                    "BEHAVIOR_UNBOUND_READING",
                    f"behavior {behavior.name!r} declares reading {reading.name!r} "
                    "without an instance_id.",
                    fix_hint="Bind the reading to the instance that produces it.",
                )
            )
            continue

        instance = spec.instance(reading.instance_id)
        if instance is None:
            issues.append(
                issue(
                    "BEHAVIOR_UNKNOWN_READING",
                    f"behavior {behavior.name!r} declares reading {reading.name!r} "
                    f"on unknown instance {reading.instance_id!r}.",
                    part_ids=[reading.instance_id],
                    fix_hint=f"Use one of the instances: {sorted(instances)}.",
                )
            )
            continue

        part = catalog.get_or_none(instance.part_id)
        if part is None:
            continue
        provided = {r.name: r.unit for r in part.firmware.readings}
        if reading.name not in provided:
            issues.append(
                issue(
                    "BEHAVIOR_UNKNOWN_READING",
                    f"{reading.instance_id} ({part.id}) does not provide reading "
                    f"{reading.name!r}.",
                    part_ids=[reading.instance_id],
                    fix_hint=f"Use one of the readings: {sorted(provided)}.",
                )
            )
            continue
        if reading.unit and reading.unit != provided[reading.name]:
            issues.append(
                issue(
                    "BEHAVIOR_UNIT_MISMATCH",
                    f"reading {reading.name!r} on {reading.instance_id} is in "
                    f"{provided[reading.name]!r}, not {reading.unit!r}.",
                    part_ids=[reading.instance_id],
                    fix_hint="Use the unit the catalog reading declares.",
                )
            )
    return issues


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
    instances = [part_ref.instance_id for part_ref in spec.parts]

    for behavior in spec.behaviors:
        issues.extend(_reading_issues(spec, catalog, behavior, instances))
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
