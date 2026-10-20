"""V5 logic levels: a >3.3 V part output into a GPIO is an error.

ESP32 GPIOs are 3.3 V. If a part whose output logic exceeds 3.3 V drives a GPIO
directly, the GPIO can be damaged. This is allowed only when the net passes
through the voltage-divider part (``voltage_divider_5v_3v3``). The fix_hint
always names that divider.
"""

from __future__ import annotations

from sparkplan.models.buildspec import SupportedBuild
from sparkplan.models.catalog import BoardModel, Catalog, PinDirection
from sparkplan.models.spec import Issue
from sparkplan.validators.phase2.report import issue

DIVIDER_ID = "voltage_divider_5v_3v3"
LOGIC_LIMIT_V = 3.3


def validate_logic_levels(spec: SupportedBuild, catalog: Catalog, board: BoardModel) -> list[Issue]:
    issues: list[Issue] = []

    # instance ids that are a voltage divider in this spec
    dividers = {p.instance_id for p in spec.parts if p.part_id == DIVIDER_ID}

    for conn in spec.connections:
        if conn.signal_role in {"rail", "gnd", "passive"}:
            continue
        part = (
            catalog.get_or_none(spec.part_on(conn.from_instance))
            if spec.instance(conn.from_instance)
            else None
        )
        if part is None or part.category.value == "board":
            continue
        pin = part.pin(conn.from_pin)
        if pin is None:
            continue
        # only outputs/bidirectional pins can drive the ESP32
        if pin.direction not in {PinDirection.OUT, PinDirection.BIDIR}:
            continue
        if part.logic_level_v <= LOGIC_LIMIT_V:
            continue

        # allowed if the net passes through a divider (either endpoint)
        if conn.from_instance in dividers or conn.to_instance in dividers:
            continue
        # allowed if the (peripheral) side is the divider itself
        if any(
            c.from_instance == conn.from_instance
            and conn.from_pin == c.from_pin
            and c.to_instance in dividers
            for c in spec.connections
        ):
            continue

        issues.append(
            issue(
                "LOGIC_LEVEL_HIGH",
                f"{conn.from_instance} ({part.id}) pin {pin.name} outputs "
                f"{part.logic_level_v} V into {conn.to_instance} {conn.to_pin}; "
                f"GPIOs tolerate at most {LOGIC_LIMIT_V} V.",
                part_ids=[conn.from_instance, conn.to_instance],
                fix_hint=(
                    "Route this signal through the voltage divider "
                    f"('{DIVIDER_ID}') to bring it under 3.3 V."
                ),
            )
        )

    return issues
