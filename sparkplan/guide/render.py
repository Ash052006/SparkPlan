"""Deterministic build-guide rendering.

No LLM: the guide is assembled from the validated BuildSpec, catalog
data, enclosure plan, and firmware result.
"""

from __future__ import annotations

from sparkplan.catalog import Catalog
from sparkplan.firmware.generate import FirmwareResult
from sparkplan.geometry.params import EnclosurePlan
from sparkplan.models.spec import BuildSpec, Issue


def _wiring_rows(spec: BuildSpec, catalog: Catalog) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for conn in spec.connections:
        inst = spec.instance(conn.instance_id)
        if inst is None:
            continue
        part = catalog.part_or_none(inst.part_id)
        if part is None:
            continue
        pin = part.pin(conn.peripheral_pin)
        note = ""
        if pin is not None and pin.level_shift_required:
            note = "5 V logic: level-shift to 3.3 V"
        elif pin is not None and pin.pull == "external_10k":
            note = "10 k pull-up to 3V3"
        rows.append(
            (
                inst.instance_id,
                part.name,
                conn.peripheral_pin,
                conn.controller_pin + (f" ({note})" if note else ""),
            )
        )
    return rows


def _bom_rows(spec: BuildSpec, catalog: Catalog) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for inst in spec.parts:
        part = catalog.part_or_none(inst.part_id)
        if part is None:
            continue
        rows.append(
            (
                inst.instance_id,
                part.name,
                part.supply.rail,
                f"{part.supply.i_ma_typ:.0f}/{part.supply.i_ma_max:.0f}",
            )
        )
    return rows


def render_guide(
    spec: BuildSpec,
    catalog: Catalog,
    enclosure: EnclosurePlan | None,
    firmware: FirmwareResult | None,
    issues: list[Issue] | None = None,
) -> str:
    lines: list[str] = []
    add = lines.append

    add(f"# Build guide: {spec.idea}")
    add("")
    add(f"Controller: **{catalog.controller.name}** (`{catalog.controller.part_id}`).")
    add("Units: millimetres, volts, milliamps. Signal wires are 3.3 V logic.")
    add("")

    add("## 1. Bill of materials")
    add("")
    add("| instance | part | rail | typ/max mA |")
    add("|---|---|---|---|")
    for instance_id, name, rail, current in _bom_rows(spec, catalog):
        add(f"| {instance_id} | {name} | {rail} | {current} |")
    add("")

    add("## 2. Wiring")
    add("")
    add("| instance | part pin | ESP32 pin | notes |")
    add("|---|---|---|---|")
    for instance_id, name, pin, target in _wiring_rows(spec, catalog):
        add(f"| {instance_id} | {pin} | {target} | {name} |")
    add("")
    add("Power and ground are not listed: connect every module's VCC and GND")
    add("to the rail named in the BOM and to a common ground.")
    add("")

    if enclosure is not None:
        p = enclosure.params
        add("## 3. Enclosure")
        add("")
        add(
            f"- Base outer size: {p.outer_x_mm:.1f} x {p.outer_y_mm:.1f} x "
            f"{p.base_height_mm:.1f} mm"
        )
        add(
            f"- Lid: {p.outer_x_mm:.1f} x {p.outer_y_mm:.1f} x "
            f"{p.lid_mm + p.lid_lip_mm:.1f} mm (with alignment lip)"
        )
        add(
            f"- Wall {p.wall_mm:.1f} mm, floor {p.floor_mm:.1f} mm, "
            f"M2.5 self-tapping screw bosses (hole {p.boss_hole_mm:.1f} mm)"
        )
        add(
            "- Print: PETG or PLA, 0.2 mm layers, 3 perimeters, 15% infill, "
            "no supports for the base"
        )
        add(
            "- Mount the ESP32 on M2.5 standoffs; route peripheral wires out "
            "through the side notches"
        )
        usb = [c for c in p.cutouts if c.kind == "usb"]
        if usb:
            add(f"- USB cutout on the -X wall, {usb[0].width_mm:.1f} x {usb[0].height_mm:.1f} mm")
        add("")

    add("## 4. Firmware")
    add("")
    if firmware is not None:
        if firmware.libraries:
            add("Libraries (install with `arduino-cli lib install ...`):")
            for lib in firmware.libraries:
                add(f"- {lib}")
        else:
            add("No external libraries required.")
        add("")
        add("Flash:")
        add("")
        add("```sh")
        add("arduino-cli compile --fqbn esp32:esp32:esp32 sparkplan_ino")
        add("arduino-cli upload  --fqbn esp32:esp32:esp32 -p <PORT> sparkplan_ino")
        add("```")
    else:
        add("Firmware not generated.")
    add("")

    add("## 5. Assembly")
    add("")
    steps = [
        "Print the base and lid; test-fit the ESP32 on standoffs.",
        "Mount the ESP32 and every peripheral module in the positions shown "
        "by the enclosure layout.",
        "Wire signals per the wiring table; double-check no 5 V line reaches an ESP32 GPIO.",
        "Wire power: 3V3 rail parts to 3V3, 5 V rail parts to 5V, grounds common.",
        "Compile and flash the firmware; open the serial monitor at 115200 baud.",
        "Verify each action: sensors report, actuators move, display shows values.",
        "Fit the lid with four M2.5 x 10 mm self-tapping screws.",
    ]
    for i, step in enumerate(steps, start=1):
        add(f"{i}. {step}")
    add("")

    warnings = [i for i in (issues or []) if i.severity in ("warning", "error")]
    if warnings:
        add("## Notes and cautions")
        add("")
        for issue in warnings:
            add(f"- **{issue.code}** ({issue.severity}): {issue.message}")
            if issue.fix_hint:
                add(f"  - {issue.fix_hint}")
        add("")

    return "\n".join(lines)
