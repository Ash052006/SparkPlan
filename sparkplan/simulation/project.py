"""Deterministic Wokwi project generation from a validated SupportedBuild.

The LLM never writes diagram, scenario, or configuration files. Everything is
derived from the catalog wiring and the validated build spec. Components the
catalog cannot represent in Wokwi are reported, not approximated.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from sparkplan.firmware.template import board_pin_to_gpio
from sparkplan.models.buildspec import Connection, SupportedBuild
from sparkplan.models.catalog import Catalog, Category, Part
from sparkplan.models.spec import Issue
from sparkplan.simulation.models import WokwiProject

SCENARIO_MARKER = "SPARKPLAN_READY"
SCREENSHOT_NAME = "screenshot.png"
SERIAL_LOG_NAME = "serial.log"

_WIRE_COLORS = {
    "rail": "red",
    "gnd": "black",
    "sda": "blue",
    "scl": "yellow",
    "passive": "gray",
    "signal": "green",
}


def _issue(code: str, message: str, *, severity: str = "warning", fix_hint: str = "") -> Issue:
    return Issue(
        code=code,
        severity=severity,  # type: ignore[arg-type]
        message=message,
        part_ids=[],
        fix_hint=fix_hint,
    )


def _simulator_pin(part: Part, pin_name: str) -> str | None:
    """Resolve a component or board pin name to its simulator identifier."""
    if part.wokwi is None:
        return None
    pin_map = part.wokwi.pin_map
    if pin_name in pin_map:
        return pin_map[pin_name]
    if part.category is Category.BOARD:
        gpio = board_pin_to_gpio(pin_name)
        if gpio is not None:
            for candidate in (f"D{gpio}", f"GPIO{gpio}", str(gpio)):
                if candidate in pin_map:
                    return pin_map[candidate]
    return None


def _connection_key(connection: Connection) -> tuple[str, str, str, str]:
    return (
        connection.from_instance,
        connection.from_pin,
        connection.to_instance,
        connection.to_pin,
    )


def _part_entry(part: Part, instance_id: str, top: float, left: float) -> dict[str, object]:
    assert part.wokwi is not None
    return {
        "type": part.wokwi.type,
        "id": instance_id,
        "top": top,
        "left": left,
        "attrs": dict(sorted(part.wokwi.attrs.items())),
    }


def _scenario_text(screenshot_part_id: str | None) -> str:
    lines = [
        "name: 'SparkPlan simulation'",
        "version: 1",
        "author: 'SparkPlan'",
        "steps:",
        f"  - wait-serial: '{SCENARIO_MARKER}'",
        "  - delay: 1000ms",
    ]
    if screenshot_part_id is not None:
        lines.extend(
            [
                "  - take-screenshot:",
                f"      part-id: '{screenshot_part_id}'",
                f"      save-to: '{SCREENSHOT_NAME}'",
            ]
        )
    return "\n".join(lines) + "\n"


def _config_text() -> str:
    return "[wokwi]\n" "version = 1\n" "firmware = 'firmware.bin'\n" "elf = 'firmware.elf'\n"


def generate_wokwi_project(
    spec: SupportedBuild,
    catalog: Catalog,
    project_dir: str | Path,
    firmware_bin: str | Path | None,
    firmware_elf: str | Path | None,
) -> WokwiProject:
    """Write a Wokwi project for the representable parts of ``spec``.

    Unsupported catalog parts and connections are omitted and reported as
    warnings. Missing firmware or a missing board are errors: the project is
    still written for inspection, but it must not be executed as a success.
    """
    root = Path(project_dir)
    root.mkdir(parents=True, exist_ok=True)
    issues: list[Issue] = []
    unsupported_instances: list[str] = []
    unsupported_part_ids: list[str] = []

    {ref.instance_id: ref for ref in spec.parts}
    supported: dict[str, tuple[str, Part]] = {}
    board_instance: str | None = None
    board_part: Part | None = None

    for ref in sorted(spec.parts, key=lambda item: item.instance_id):
        part = catalog.get_or_none(ref.part_id)
        if part is None:
            unsupported_instances.append(ref.instance_id)
            unsupported_part_ids.append(ref.part_id)
            issues.append(
                _issue(
                    "SIMULATION_UNKNOWN_PART",
                    f"instance {ref.instance_id!r} uses unknown catalog part {ref.part_id!r}",
                )
            )
            continue
        if ref.quantity != 1:
            unsupported_instances.append(ref.instance_id)
            unsupported_part_ids.append(ref.part_id)
            issues.append(
                _issue(
                    "SIMULATION_QUANTITY_NOT_REPRESENTED",
                    f"instance {ref.instance_id!r} requests quantity {ref.quantity}; "
                    "the simulator represents one component per instance",
                )
            )
            continue
        if part.wokwi is None:
            unsupported_instances.append(ref.instance_id)
            unsupported_part_ids.append(ref.part_id)
            issues.append(
                _issue(
                    "SIMULATION_UNSUPPORTED_PART",
                    f"part {ref.part_id!r} has no Wokwi representation; "
                    f"instance {ref.instance_id!r} is omitted",
                )
            )
            continue
        if part.category is Category.BOARD:
            if board_instance is not None:
                unsupported_instances.append(ref.instance_id)
                unsupported_part_ids.append(ref.part_id)
                issues.append(
                    _issue(
                        "SIMULATION_MULTIPLE_BOARDS",
                        f"instance {ref.instance_id!r} is an additional board and cannot "
                        f"be represented alongside {board_instance!r}",
                    )
                )
                continue
            board_instance = ref.instance_id
            board_part = part
        supported[ref.instance_id] = (ref.part_id, part)

    if board_instance is None:
        issues.append(
            _issue(
                "SIMULATION_BOARD_MISSING",
                "the build has no board that can be represented in Wokwi",
                severity="error",
                fix_hint="Add the supported ESP32 DevKit board to the build.",
            )
        )

    parts: list[dict[str, object]] = []
    for index, (instance_id, (_, part)) in enumerate(
        sorted(supported.items(), key=lambda item: (item[0] != board_instance, item[0]))
    ):
        if instance_id == board_instance:
            top, left = 0.0, 0.0
        else:
            slot = index - 1
            top = 120.0 + (slot // 4) * 120.0
            left = -220.0 + (slot % 4) * 140.0
        parts.append(_part_entry(part, instance_id, top, left))
    assert board_part is None or board_instance is not None

    wires: dict[tuple[str, str, str, str], list[object]] = {}
    for connection in spec.connections:
        if connection.from_instance not in supported or connection.to_instance not in supported:
            continue
        source = supported[connection.from_instance][1]
        target = supported[connection.to_instance][1]
        source_pin = _simulator_pin(source, connection.from_pin)
        target_pin = _simulator_pin(target, connection.to_pin)
        if source_pin is None or target_pin is None:
            issues.append(
                _issue(
                    "SIMULATION_UNSUPPORTED_CONNECTION",
                    f"connection {connection.from_instance}.{connection.from_pin} -> "
                    f"{connection.to_instance}.{connection.to_pin} has no simulator pin "
                    "mapping and is omitted",
                )
            )
            continue
        endpoints = (
            f"{connection.from_instance}:{source_pin}",
            f"{connection.to_instance}:{target_pin}",
        )
        wires[_connection_key(connection)] = [
            endpoints[0],
            endpoints[1],
            _WIRE_COLORS.get(connection.signal_role, "green"),
            [],
        ]
    if len(unsupported_instances) != len(set(unsupported_instances)):
        unsupported_instances = sorted(set(unsupported_instances))
    if len(unsupported_part_ids) != len(set(unsupported_part_ids)):
        unsupported_part_ids = sorted(set(unsupported_part_ids))

    diagram = {
        "version": 1,
        "author": "SparkPlan",
        "editor": "wokwi",
        "parts": parts,
        "connections": [wires[key] for key in sorted(wires)],
        "dependencies": {},
    }
    diagram_path = root / "diagram.json"
    diagram_path.write_text(json.dumps(diagram, indent=2) + "\n", encoding="utf-8")

    config_path = root / "wokwi.toml"
    config_path.write_text(_config_text(), encoding="utf-8")

    screenshot_target = board_instance
    for instance_id, (_, part) in supported.items():
        if part.wokwi is not None and part.wokwi.type == "board-ssd1306":
            screenshot_target = instance_id
            break
    scenario_path = root / "scenario.yaml"
    scenario_path.write_text(_scenario_text(screenshot_target), encoding="utf-8")

    for source, name in ((firmware_bin, "firmware.bin"), (firmware_elf, "firmware.elf")):
        destination = root / name
        source_path = Path(source) if source is not None else None
        if source_path is None or not source_path.is_file() or source_path.stat().st_size <= 0:
            issues.append(
                _issue(
                    "SIMULATION_FIRMWARE_MISSING",
                    f"firmware artifact {name!r} is missing or empty; the simulator "
                    "cannot boot the build",
                    severity="error",
                    fix_hint="Compile the sketch successfully and persist the firmware outputs.",
                )
            )
            continue
        if source_path.resolve() != destination.resolve():
            shutil.copyfile(source_path, destination)

    return WokwiProject(
        project_dir=root,
        diagram_path=diagram_path,
        config_path=config_path,
        scenario_path=scenario_path,
        serial_log_path=root / SERIAL_LOG_NAME,
        screenshot_path=root / SCREENSHOT_NAME,
        board_instance_id=board_instance,
        screenshot_part_id=screenshot_target,
        unsupported_instances=unsupported_instances,
        unsupported_part_ids=unsupported_part_ids,
        issues=issues,
    )
