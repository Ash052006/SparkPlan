"""V4 power: rails, grounds and current budget.

* each part VCC connection must name a rail whose voltage is inside the part's
  supply range (error)
* every part with a GND pin must have at least one GND connection (error)
* the sum of current per rail is checked against the rail budget:
  - errors when the budget is exceeded
  - warnings above 80 % of the budget
  Peak current is used (and named in the message) for parts that draw surges -
  servos and addressable LED strips - otherwise typical current is used.
"""

from __future__ import annotations

from sparkplan.models.buildspec import SupportedBuild
from sparkplan.models.catalog import BoardModel, Catalog, Part
from sparkplan.models.spec import Issue
from sparkplan.validators.phase2.report import issue

PEAK_CURRENT_CAPABILITIES = {"rotates_arm", "shows_color"}
WARN_FRACTION = 0.80


def _part_for(spec: SupportedBuild, catalog: Catalog, instance_id: str) -> Part | None:
    part_id = spec.part_on(instance_id) if spec.instance(instance_id) else None
    return catalog.get_or_none(part_id) if part_id else None


def _uses_peak_current(part: Part) -> bool:
    return any(part.has_capability(c) for c in PEAK_CURRENT_CAPABILITIES)


def _has_board_ground_path(
    spec: SupportedBuild,
    catalog: Catalog,
    grounded_pins: set[tuple[str, str]],
    instance_id: str,
    pin_name: str,
) -> bool:
    """True when a GND pin reaches board GND directly or through a series passive."""
    if (instance_id, pin_name) in grounded_pins:
        return True
    for conn in spec.connections:
        if conn.from_instance == instance_id and conn.from_pin == pin_name:
            other_instance, other_pin = conn.to_instance, conn.to_pin
        elif conn.to_instance == instance_id and conn.to_pin == pin_name:
            other_instance, other_pin = conn.from_instance, conn.from_pin
        else:
            continue
        passive = _part_for(spec, catalog, other_instance)
        if passive is None or passive.category.value != "passive" or len(passive.pins) != 2:
            continue
        for pin in passive.pins:
            if pin.name != other_pin and (other_instance, pin.name) in grounded_pins:
                return True
    return False


def validate_power(spec: SupportedBuild, catalog: Catalog, board: BoardModel) -> list[Issue]:
    issues: list[Issue] = []

    rails = {r.name.lower(): r for r in board.rails}

    # instance id -> rail name
    vcc_rail: dict[str, str] = {}
    board_instances = {
        ref.instance_id
        for ref in spec.parts
        if (part := catalog.get_or_none(ref.part_id)) is not None and part.category.value == "board"
    }
    grounded_pins: set[tuple[str, str]] = set()
    for conn in spec.connections:
        if conn.signal_role == "rail":
            vcc_rail.setdefault(conn.from_instance, conn.rail_name or conn.to_pin)
        elif conn.signal_role == "gnd":
            if conn.from_instance in board_instances and conn.from_pin.upper() == "GND":
                grounded_pins.add((conn.to_instance, conn.to_pin))
            elif conn.to_instance in board_instances and conn.to_pin.upper() == "GND":
                grounded_pins.add((conn.from_instance, conn.from_pin))

    rail_current_ma: dict[str, float] = {r.name: 0.0 for r in board.rails}
    rail_users: dict[str, list[str]] = {r.name: [] for r in board.rails}
    rail_peak: dict[str, bool] = {r.name: False for r in board.rails}

    for part_ref in spec.parts:
        part = catalog.get_or_none(part_ref.part_id)
        if part is None:
            continue  # V1 reports unknown parts
        if part.category.value == "board":
            continue

        has_vcc_pin = any(p.role.value == "vcc" for p in part.pins)
        has_gnd_pin = any(p.role.value == "gnd" for p in part.pins)

        # VCC to a rail inside its supply range
        rail_name = vcc_rail.get(part_ref.instance_id)
        if rail_name is None:
            if has_vcc_pin:
                issues.append(
                    issue(
                        "NO_POWER",
                        f"{part_ref.instance_id} ({part.id}) has no VCC connection to a rail.",
                        part_ids=[part_ref.instance_id],
                        fix_hint="Connect the part's VCC pin to the 3V3 or 5V rail.",
                    )
                )
        else:
            rail = rails.get(rail_name.lower())
            if rail is None:
                issues.append(
                    issue(
                        "UNKNOWN_RAIL",
                        f"{part_ref.instance_id} is powered from unknown rail {rail_name!r}.",
                        part_ids=[part_ref.instance_id],
                        fix_hint=f"Use one of the board rails: {sorted(rails)}.",
                    )
                )
            else:
                if not (
                    part.supply_voltage_v.min_v <= rail.voltage_v <= part.supply_voltage_v.max_v
                ):
                    issues.append(
                        issue(
                            "RAIL_OUT_OF_RANGE",
                            f"{part_ref.instance_id} ({part.id}) accepts "
                            f"{part.supply_voltage_v.min_v}-{part.supply_voltage_v.max_v} V but "
                            f"rail {rail.name} is {rail.voltage_v} V.",
                            part_ids=[part_ref.instance_id],
                            fix_hint="Move the part to a rail inside its supply range.",
                        )
                    )
                rail_current_ma[rail.name] = rail_current_ma.get(rail.name, 0.0) + _draw(part)
                rail_users.setdefault(rail.name, []).append(part_ref.instance_id)
                if _uses_peak_current(part):
                    rail_peak[rail.name] = True

        # GND connected (only parts that expose a GND pin need one)
        if has_gnd_pin:
            gnd_pins = [p.name for p in part.pins if p.role.value == "gnd"]
            if not any(
                _has_board_ground_path(spec, catalog, grounded_pins, part_ref.instance_id, gnd_pin)
                for gnd_pin in gnd_pins
            ):
                issues.append(
                    issue(
                        "NO_GROUND",
                        f"{part_ref.instance_id} ({part.id}) has no GND connection.",
                        part_ids=[part_ref.instance_id],
                        fix_hint="Connect the part's GND pin to a board GND.",
                    )
                )

    # budget check
    for rail_name, used_ma in rail_current_ma.items():
        rail = rails.get(rail_name.lower())
        if rail is None or used_ma <= 0:
            continue
        budget = rail.max_current_ma
        basis = "peak current" if rail_peak[rail.name] else "typical current"
        if used_ma > budget:
            issues.append(
                issue(
                    "RAIL_OVER_BUDGET",
                    f"rail {rail.name} draws {used_ma:.0f} mA against a {budget:.0f} mA budget "
                    f"using {basis} ({_users(rail_users.get(rail.name, []))}).",
                    part_ids=rail_users.get(rail.name, []),
                    fix_hint="Use a separate supply or move parts to another rail.",
                )
            )
        elif used_ma > WARN_FRACTION * budget:
            who = _users(rail_users.get(rail.name, []))
            issues.append(
                issue(
                    "RAIL_NEAR_BUDGET",
                    f"rail {rail.name} draws {used_ma:.0f} mA, above 80% of its "
                    f"{budget:.0f} mA budget using {basis} ({who}).",
                    severity="warning",
                    part_ids=rail_users.get(rail.name, []),
                    fix_hint="Leave headroom for current surges.",
                )
            )

    return issues


def _draw(part: Part) -> float:
    if _uses_peak_current(part):
        return part.peak_current_ma
    return part.typical_current_ma


def _users(instance_ids: list[str]) -> str:
    return ", ".join(instance_ids) if instance_ids else "no parts"
