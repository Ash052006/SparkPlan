"""Deterministic build-guide rendering.

No LLM: the guide is assembled from the validated BuildSpec, catalog
data, enclosure plan, and firmware result.
"""

from __future__ import annotations

from sparkplan.catalog import Catalog
from sparkplan.firmware.generate import FirmwareResult
from sparkplan.geometry.enclosure import EnclosureLayout
from sparkplan.geometry.params import EnclosurePlan
from sparkplan.guide.models import RunVerification
from sparkplan.models.buildspec import Connection, SupportedBuild
from sparkplan.models.catalog import Catalog as CatalogV2
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


def _cell(value: str) -> str:
    """Keep Markdown table cells on one line and safe from column escapes."""
    return " ".join(value.replace("|", "/").split())


def _wire_colour(connection: Connection) -> str:
    """A deterministic, conventional wire colour for one connection."""
    if connection.signal_role == "rail":
        rail_name = (connection.rail_name or "").lower()
        if "5" in rail_name:
            return "orange"
        return "red"
    return {
        "gnd": "black",
        "sda": "blue",
        "scl": "yellow",
        "passive": "white",
        "signal": "green",
    }[connection.signal_role]


def _mermaid_label(value: str) -> str:
    """Escape user/catalog text before embedding it in a Mermaid label."""
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("|", "/")
        .replace("[", "(")
        .replace("]", ")")
        .replace("{", "(")
        .replace("}", ")")
        .replace("\n", " ")
    )


def _verification_cell(value: bool | str) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "skipped"


def render_supported_guide(
    spec: SupportedBuild,
    catalog: CatalogV2,
    enclosure: EnclosureLayout | None = None,
    firmware: FirmwareResult | None = None,
    issues: list[Issue] | None = None,
    verification: RunVerification | None = None,
) -> str:
    """Render a deterministic guide for a Phase 3 ``SupportedBuild``.

    The enclosure and firmware arguments are optional so a guide can still be
    produced when one of those stages failed. Every value comes from the
    validated spec, the catalog, or an explicit deterministic stage result.
    """
    lines: list[str] = []
    add = lines.append
    verified = verification or RunVerification()

    add(f"# Build guide: {spec.title}")
    add("")
    add(spec.summary)
    add("")
    board = catalog.board()
    add(f"Controller: **{board.name}** (`{board.id}`).")
    add("Units: millimetres, volts, milliamps. Signal wires are 3.3 V logic.")
    add("")

    add("## 1. Bill of materials")
    add("")
    add("| instance | part | quantity | role | supply | typ/peak mA | mount |")
    add("|---|---|---|---|---|---|---|")
    for ref in spec.parts:
        part = catalog.get_or_none(ref.part_id)
        if part is None:
            continue
        role = ref.label.strip() or part.category.value
        supply = f"{part.supply_voltage_v.min_v:g}-{part.supply_voltage_v.max_v:g} V"
        current = f"{part.typical_current_ma:.0f}/{part.peak_current_ma:.0f}"
        add(
            f"| {_cell(ref.instance_id)} | {_cell(part.name)} | {ref.quantity} | "
            f"{_cell(role)} | {_cell(supply)} | {current} | {part.mount.value} |"
        )
    add("")

    add("## 2. Wiring")
    add("")
    add("| from pin | to pin | signal role | suggested wire colour | notes |")
    add("|---|---|---|---|---|")
    for connection in spec.connections:
        role = connection.signal_role
        if connection.rail_name:
            role = f"{role} ({connection.rail_name})"
        add(
            f"| {_cell(connection.from_instance)}.{_cell(connection.from_pin)} | "
            f"{_cell(connection.to_instance)}.{_cell(connection.to_pin)} | {_cell(role)} | "
            f"{_wire_colour(connection)} | {_cell(connection.note)} |"
        )
    add("")

    add("### Mermaid wiring diagram")
    add("")
    add("```mermaid")
    add("graph LR")
    node_ids: dict[str, str] = {}
    for index, ref in enumerate(spec.parts):
        part = catalog.get_or_none(ref.part_id)
        if part is None:
            continue
        node_id = f"N{index}"
        node_ids[ref.instance_id] = node_id
        add(f'  {node_id}["{_mermaid_label(ref.instance_id)}<br/>{_mermaid_label(part.name)}"]')
    for connection in spec.connections:
        from_node = node_ids.get(connection.from_instance)
        to_node = node_ids.get(connection.to_instance)
        if from_node is None or to_node is None:
            continue
        label = f"{connection.signal_role} / {_wire_colour(connection)}"
        add(f'  {from_node} -->|"{_mermaid_label(label)}"| {to_node}')
    add("```")
    add("")

    add("## 3. Power notes")
    add("")
    board_model = board.board
    if board_model is not None:
        for rail in board_model.rails:
            add(
                f"- **{_cell(rail.name)}**: {rail.voltage_v:g} V, budget "
                f"{rail.max_current_ma:.0f} mA. {_cell(rail.source_note)}"
            )
        if any(rail.verify_against_datasheet for rail in board_model.rails):
            add(
                "- Verify rail limits against the board and part datasheets before "
                "powering the build."
            )
    add("- Connect every module ground to one common ground.")
    add("- Never feed 5 V into an ESP32 GPIO; use a level shifter where the wiring notes say so.")
    add("")

    if spec.behaviors:
        add("## 4. Behaviour")
        add("")
        for behavior in spec.behaviors:
            description = behavior.description or "No description."
            add(f"- **{behavior.name}**: {description}")
        add("")

    add("## 5. Enclosure")
    add("")
    if enclosure is None:
        add("No enclosure layout was generated. Do not print an enclosure from this run.")
    else:
        config = enclosure.config
        add(
            f"- Base outer size: {enclosure.outer_x_mm:.1f} x {enclosure.outer_y_mm:.1f} x "
            f"{config.floor_mm + enclosure.inner_z_mm:.1f} mm"
        )
        add(
            f"- Lid: {enclosure.outer_x_mm:.1f} x {enclosure.outer_y_mm:.1f} x "
            f"{config.lid_mm + config.lid_lip_mm:.1f} mm (with alignment lip)"
        )
        add(
            f"- Wall {config.wall_mm:.1f} mm, floor {config.floor_mm:.1f} mm, "
            f"{len(enclosure.posts)} corner screw post(s)"
        )
        if enclosure.parts:
            add("")
            add("| instance | mount | x | y | z |")
            add("|---|---|---|---|---|")
            for part in enclosure.parts:
                add(
                    f"| {part.instance_id} | {part.mount.value} | {part.x_mm:.1f} | "
                    f"{part.y_mm:.1f} | {part.z_mm:.1f} |"
                )
        if enclosure.openings:
            add("")
            add(f"- Openings: {len(enclosure.openings)}")
    add("")

    add("## 6. Print settings")
    add("")
    add("- Material: PLA or PETG")
    add("- Layer height: 0.2 mm")
    add("- Perimeters: 3")
    add("- Infill: 15%")
    add("- Supports: none for the base unless the generated layout notes otherwise")
    add("")

    add("## 7. Firmware")
    add("")
    if firmware is not None:
        if firmware.libraries:
            add("Libraries (install with `arduino-cli lib install ...`):")
            for library in firmware.libraries:
                add(f"- {library}")
        else:
            add("No external libraries required.")
        add("")
        add("Flash:")
        add("")
        add("```sh")
        add("arduino-cli compile --fqbn esp32:esp32:esp32 sketch")
        add("arduino-cli upload  --fqbn esp32:esp32:esp32 -p <PORT> sketch")
        add("```")
    else:
        add("Firmware was not generated.")
    add("")

    add("## 8. Assembly")
    add("")
    steps = [
        "Print the base and lid using the print settings above; test-fit the ESP32 on standoffs.",
        "Install the standoffs, then mount the ESP32 and every peripheral module in the layout "
        "positions.",
        "Route wires using the wiring table and Mermaid diagram; keep signal and power runs "
        "separated.",
        "Connect power and common ground, then inspect the wiring before applying power.",
        "Compile and flash the firmware; open the serial monitor at 115200 baud.",
        "Run the pre-flight test checklist before closing the enclosure.",
        "Fit the lid and secure it with the specified M2.5 screws.",
    ]
    for number, step in enumerate(steps, start=1):
        add(f"{number}. {step}")
    add("")

    warnings = [issue for issue in (issues or []) if issue.severity in ("warning", "error")]
    if warnings:
        add("## Notes and cautions")
        add("")
        for issue in warnings:
            add(f"- **{issue.code}** ({issue.severity}): {issue.message}")
            if issue.fix_hint:
                add(f"  - {issue.fix_hint}")
        add("")

    add("## 9. Pre-flight test checklist")
    add("")
    add("- [ ] Confirm no loose wire strands or accidental connections between adjacent pins.")
    add("- [ ] Measure each board rail with a multimeter before connecting modules.")
    add("- [ ] Confirm every ground is connected to the common ground.")
    for behavior in spec.behaviors:
        add(f"- [ ] Confirm the **{behavior.name}** behaviour works as described.")
    add("- [ ] Confirm the enclosure closes without pinching wires or blocking openings.")
    add("")

    add("## 10. Verification")
    add("")
    add("| Check | Result |")
    add("|---|---|")
    add(f"| Validated | {_verification_cell(verified.validated)} |")
    add(f"| Compiled | {_verification_cell(verified.compiled)} |")
    add(f"| Sliced | {_verification_cell(verified.sliced)} |")
    add(f"| Simulated | {_verification_cell(verified.simulated)} |")
    add("")
    add("Not physically built.")
    return "\n".join(lines)
