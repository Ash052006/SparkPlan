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
from sparkplan.validators.phase2.pins_check import _controller_gpio
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


def _actuator_has_board_control(
    spec: SupportedBuild, catalog: Catalog, board: BoardModel, instance_id: str
) -> bool:
    """True when an actuator pin reaches an output-capable GPIO directly or via a series passive."""
    for conn in spec.connections:
        if conn.signal_role in {"rail", "gnd", "passive"}:
            continue
        if conn.from_instance == instance_id:
            other_instance, other_pin = conn.to_instance, conn.to_pin
        elif conn.to_instance == instance_id:
            other_instance, other_pin = conn.from_instance, conn.from_pin
        else:
            continue
        if spec.instance(other_instance) is None:
            continue
        other_part = catalog.get_or_none(spec.part_on(other_instance))
        if other_part is None:
            continue
        if other_part.category.value == "board":
            gpio = _controller_gpio(other_pin)
            if gpio is None:
                continue
            board_pin = board.pin(gpio)
            if (
                board_pin is not None
                and board_pin.caps.output
                and not board_pin.input_only
                and not board_pin.forbidden
            ):
                return True
        elif other_part.category.value == "passive" and len(other_part.pins) == 2:
            for pconn in spec.connections:
                if pconn.signal_role in {"rail", "gnd", "passive"}:
                    continue
                if pconn.from_instance == other_instance and pconn.from_pin != other_pin:
                    b_instance, b_pin = pconn.to_instance, pconn.to_pin
                elif pconn.to_instance == other_instance and pconn.to_pin != other_pin:
                    b_instance, b_pin = pconn.from_instance, pconn.from_pin
                else:
                    continue
                b_part = catalog.get_or_none(spec.part_on(b_instance))
                if b_part is not None and b_part.category.value == "board":
                    gpio = _controller_gpio(b_pin)
                    if gpio is None:
                        continue
                    board_pin = board.pin(gpio)
                    if (
                        board_pin is not None
                        and board_pin.caps.output
                        and not board_pin.input_only
                        and not board_pin.forbidden
                    ):
                        return True
    return False


def validate_behaviors(spec: SupportedBuild, catalog: Catalog, board: BoardModel) -> list[Issue]:
    issues: list[Issue] = []
    readings = _known_readings(spec, catalog)
    actuators = _known_actuators(spec, catalog)
    instances = [part_ref.instance_id for part_ref in spec.parts]
    checked_actuators: set[str] = set()

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
            if rule.action_actuator:
                if rule.action_actuator not in actuators:
                    issues.append(
                        issue(
                            "BEHAVIOR_UNKNOWN_ACTUATOR",
                            f"rule {rule.name!r} acts on unknown actuator "
                            f"{rule.action_actuator!r}.",
                            fix_hint=f"Use one of the actuators: {sorted(actuators)}.",
                        )
                    )
                else:
                    issues.append(
                        issue(
                            "BEHAVIOR_RULE_ACTUATOR_UNSUPPORTED",
                            f"rule {rule.name!r} drives actuator {rule.action_actuator!r}, but "
                            "behavior rules do not support direct actuator actions.",
                            part_ids=[rule.action_actuator],
                            fix_hint=(
                                "Set action_reading to a valid reading name to log rule triggers, "
                                "or display values with display_lines; leave action_actuator empty."
                            ),
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

    for part_ref in spec.parts:
        part = catalog.get_or_none(part_ref.part_id)
        if part is None or part.category.value != "actuator":
            continue
        if part_ref.instance_id not in checked_actuators:
            checked_actuators.add(part_ref.instance_id)
            if not _actuator_has_board_control(spec, catalog, board, part_ref.instance_id):
                issues.append(
                    issue(
                        "ACTUATOR_NO_CONTROL_GPIO",
                        f"actuator {part_ref.instance_id!r} ({part.id}) has no control pin "
                        "connected directly to an output-capable board GPIO.",
                        part_ids=[part_ref.instance_id],
                        fix_hint=(
                            "Connect the actuator control pin to a board GPIO. "
                            "For led_5mm: ANODE -> a board GPIO, CATHODE -> "
                            "resistor_220r -> board GND; do not power the LED "
                            "from a rail."
                        ),
                    )
                )

    return issues
