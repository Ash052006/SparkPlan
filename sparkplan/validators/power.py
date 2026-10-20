"""Power-rail checks: voltage compatibility, current budgets, boost/battery pairing."""

from __future__ import annotations

from sparkplan.catalog import Catalog
from sparkplan.models.spec import BuildSpec, Issue


def _err(code: str, message: str, part_ids: list[str] | None = None, hint: str = "") -> Issue:
    return Issue(
        code=code, severity="error", message=message, part_ids=part_ids or [], fix_hint=hint
    )


def _warn(code: str, message: str, part_ids: list[str] | None = None, hint: str = "") -> Issue:
    return Issue(
        code=code, severity="warning", message=message, part_ids=part_ids or [], fix_hint=hint
    )


def _info(code: str, message: str, part_ids: list[str] | None = None, hint: str = "") -> Issue:
    return Issue(
        code=code, severity="info", message=message, part_ids=part_ids or [], fix_hint=hint
    )


def validate_power(spec: BuildSpec, catalog: Catalog) -> list[Issue]:
    issues: list[Issue] = []
    rails = catalog.power.rails

    load_3v3 = 0.0
    load_5v = rails["5v"].controller_share_ma
    has_boost = False
    has_battery = False
    boost_output_ma = catalog.power.external_supply_rules.boost_output_ma_default

    for inst in spec.parts:
        part = catalog.part_or_none(inst.part_id)
        if part is None:
            continue

        if part.power_role is not None:
            if part.power_role.kind == "boost":
                has_boost = True
                if part.power_role.output_ma:
                    boost_output_ma = part.power_role.output_ma
            elif part.power_role.kind == "battery":
                has_battery = True

        rail_name = part.supply.rail
        if rail_name == "none":
            continue
        rail = rails.get(rail_name)
        if rail is None:
            issues.append(
                _err(
                    "UNKNOWN_RAIL",
                    f"part {part.part_id} declares unknown rail {rail_name!r}.",
                    part_ids=[part.part_id],
                    hint="Catalog rails are 3v3 and 5v.",
                )
            )
            continue

        if not (part.supply.voltage_min <= rail.voltage <= part.supply.voltage_max):
            issues.append(
                _err(
                    "VOLTAGE_INCOMPATIBLE",
                    (
                        f"{inst.instance_id} ({part.part_id}) accepts "
                        f"{part.supply.voltage_min}-{part.supply.voltage_max} V but rail "
                        f"{rail_name} is {rail.voltage} V."
                    ),
                    part_ids=[part.part_id],
                    hint=part.supply.notes or "Move the part to a compatible rail or drop it.",
                )
            )

        if rail_name == "3v3":
            load_3v3 += part.supply.i_ma_max
        elif rail_name == "5v":
            load_5v += part.supply.i_ma_max

        # Analog outputs must stay within the ESP32 ADC range.
        if any(p.kind == "analog_in" for p in part.signal_pins()) and rail.voltage > 3.6:
            issues.append(
                _warn(
                    "ANALOG_OVERVOLTAGE",
                    f"{inst.instance_id} can drive AO up to {rail.voltage} V into a 3.3 V ADC.",
                    part_ids=[part.part_id],
                    hint="Power analog sensors from the 3v3 rail, or add a divider.",
                )
            )

    # 3v3 budget: fixed regulator, no external supply path.
    budget_3v3 = rails["3v3"].max_ma
    if load_3v3 > budget_3v3:
        issues.append(
            _err(
                "POWER_BUDGET_3V3",
                f"3v3 rail load {load_3v3:.0f} mA exceeds budget {budget_3v3:.0f} mA.",
                hint="Drop high-draw 3v3 parts; the 3v3 rail cannot be boosted.",
            )
        )
    elif load_3v3 > budget_3v3 * 0.8:
        issues.append(
            _warn(
                "POWER_MARGIN_3V3",
                f"3v3 rail load {load_3v3:.0f} mA is within 20% of the {budget_3v3:.0f} mA budget.",
                hint="Consider lower-draw alternatives.",
            )
        )

    # 5v budget: USB provides 500 mA unless a boost converter is present.
    budget_5v = boost_output_ma if (has_boost and has_battery) else rails["5v"].max_ma
    if has_boost and not has_battery:
        issues.append(
            _err(
                "BOOST_WITHOUT_BATTERY",
                "mt3608_boost (or similar) is present without a battery source.",
                part_ids=["mt3608_boost"],
                hint="Add battery_holder_18650 or remove the boost converter.",
            )
        )
    if has_battery and not has_boost:
        issues.append(
            _warn(
                "BATTERY_WITHOUT_BOOST",
                "battery_holder_18650 cannot feed the 5v rail directly (3.7 V < 5 V).",
                part_ids=["battery_holder_18650"],
                hint="Add mt3608_boost between the battery and the 5v rail.",
            )
        )

    if load_5v > budget_5v:
        external = has_boost and has_battery
        issues.append(
            _err(
                "POWER_BUDGET_5V",
                (
                    f"5v rail load {load_5v:.0f} mA exceeds budget {budget_5v:.0f} mA "
                    f"({'boost supply' if external else 'USB supply'})."
                ),
                hint=(
                    "Add battery_holder_18650 + mt3608_boost for a 1 A external 5V supply, "
                    "or remove high-draw parts."
                ),
            )
        )
    elif load_5v > budget_5v * 0.8 and budget_5v == rails["5v"].max_ma:
        issues.append(
            _warn(
                "POWER_MARGIN_5V",
                f"5v rail load {load_5v:.0f} mA is within 20% of the USB "
                f"{budget_5v:.0f} mA budget.",
                hint="Add battery_holder_18650 + mt3608_boost if the build must be self-powered.",
            )
        )

    if load_3v3 > 0 or load_5v > 0:
        issues.append(
            _info(
                "POWER_BUDGET_SUMMARY",
                f"Estimated max draw: 3v3={load_3v3:.0f} mA (budget {budget_3v3:.0f}), "
                f"5v={load_5v:.0f} mA (budget {budget_5v:.0f}).",
            )
        )
    return issues
