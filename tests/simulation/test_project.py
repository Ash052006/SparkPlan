from __future__ import annotations

import json
from pathlib import Path

from sparkplan.models.buildspec import parse_build_spec
from sparkplan.models.catalog import get_catalog
from sparkplan.simulation.project import _simulator_pin, generate_wokwi_project


def test_simulator_pin_resolution_board_gpio_aliases() -> None:
    catalog = get_catalog()
    board = catalog.get("esp32_devkit_30pin")

    # Numeric strings, D-prefixed strings, and GPIO-prefixed strings should all resolve
    assert _simulator_pin(board, "D4") == "D4"
    assert _simulator_pin(board, "4") == "D4"
    assert _simulator_pin(board, "GPIO4") == "D4"

    assert _simulator_pin(board, "D13") == "D13"
    assert _simulator_pin(board, "13") == "D13"
    assert _simulator_pin(board, "GPIO13") == "D13"

    assert _simulator_pin(board, "GND") == "GND.1"
    assert _simulator_pin(board, "3V3") == "3V3"


def test_generate_wokwi_project_resolves_numeric_board_pins_without_warning(
    tmp_path: Path,
) -> None:
    catalog = get_catalog()
    spec_data = {
        "status": "ok",
        "title": "Motion LED",
        "summary": "Motion sensor turns on LED",
        "parts": [
            {"instance_id": "board", "part_id": "esp32_devkit_30pin"},
            {"instance_id": "pir", "part_id": "pir_hc_sr501"},
            {"instance_id": "led", "part_id": "led_5mm"},
            {"instance_id": "res", "part_id": "resistor_220r"},
        ],
        "connections": [
            {
                "from_instance": "pir",
                "from_pin": "VCC",
                "to_instance": "board",
                "to_pin": "5V",
                "signal_role": "rail",
                "rail_name": "5V",
            },
            {
                "from_instance": "pir",
                "from_pin": "GND",
                "to_instance": "board",
                "to_pin": "GND",
                "signal_role": "gnd",
            },
            {
                "from_instance": "pir",
                "from_pin": "OUT",
                "to_instance": "board",
                "to_pin": "4",
                "signal_role": "signal",
            },
            {
                "from_instance": "led",
                "from_pin": "ANODE",
                "to_instance": "board",
                "to_pin": "13",
                "signal_role": "signal",
            },
            {
                "from_instance": "led",
                "from_pin": "CATHODE",
                "to_instance": "res",
                "to_pin": "A",
                "signal_role": "passive",
            },
            {
                "from_instance": "res",
                "from_pin": "B",
                "to_instance": "board",
                "to_pin": "GND",
                "signal_role": "gnd",
            },
        ],
        "layout": [],
        "behaviors": [],
    }
    spec = parse_build_spec(spec_data)
    bin_file = tmp_path / "firmware.bin"
    elf_file = tmp_path / "firmware.elf"
    bin_file.write_bytes(b"\x00fake")
    elf_file.write_bytes(b"\x7ffake")

    project_dir = tmp_path / "wokwi_project"
    project = generate_wokwi_project(spec, catalog, project_dir, bin_file, elf_file)

    # Check there are no SIMULATION_UNSUPPORTED_CONNECTION issues
    unsupported_conn_issues = [
        issue for issue in project.issues if issue.code == "SIMULATION_UNSUPPORTED_CONNECTION"
    ]
    assert unsupported_conn_issues == []

    # Check that diagram.json contains wires connecting to board:D4 and board:D13
    diagram_json = json.loads(project.diagram_path.read_text())
    connections = diagram_json.get("connections", [])
    wire_endpoints = [conn[:2] for conn in connections]

    assert ["pir:OUT", "board:D4"] in wire_endpoints
    assert ["led:A", "board:D13"] in wire_endpoints


def test_generate_wokwi_project_flags_unsupported_connection_for_unmapped_pin(
    tmp_path: Path,
) -> None:
    catalog = get_catalog()
    spec_data = {
        "status": "ok",
        "title": "Invalid Pin Test",
        "summary": "Tests unmapped pin reporting",
        "parts": [
            {"instance_id": "board", "part_id": "esp32_devkit_30pin"},
            {"instance_id": "led", "part_id": "led_5mm"},
        ],
        "connections": [
            {
                "from_instance": "led",
                "from_pin": "ANODE",
                "to_instance": "board",
                "to_pin": "NONEXISTENT_PIN",
                "signal_role": "signal",
            }
        ],
        "layout": [],
        "behaviors": [],
    }
    spec = parse_build_spec(spec_data)
    bin_file = tmp_path / "firmware.bin"
    elf_file = tmp_path / "firmware.elf"
    bin_file.write_bytes(b"\x00fake")
    elf_file.write_bytes(b"\x7ffake")

    project_dir = tmp_path / "wokwi_project"
    project = generate_wokwi_project(spec, catalog, project_dir, bin_file, elf_file)

    unsupported_conn_issues = [
        issue for issue in project.issues if issue.code == "SIMULATION_UNSUPPORTED_CONNECTION"
    ]
    assert len(unsupported_conn_issues) == 1
    assert "NONEXISTENT_PIN" in unsupported_conn_issues[0].message


def test_command_does_not_use_expect_text_with_scenario(tmp_path: Path) -> None:
    from sparkplan.simulation.runner import _command

    catalog = get_catalog()
    spec_data = {
        "status": "ok",
        "title": "Command Test",
        "summary": "Check command flags",
        "parts": [{"instance_id": "board", "part_id": "esp32_devkit_30pin"}],
        "connections": [],
        "layout": [],
        "behaviors": [],
    }
    spec = parse_build_spec(spec_data)
    bin_file = tmp_path / "firmware.bin"
    elf_file = tmp_path / "firmware.elf"
    bin_file.write_bytes(b"\x00fake")
    elf_file.write_bytes(b"\x7ffake")

    project = generate_wokwi_project(spec, catalog, tmp_path / "proj", bin_file, elf_file)
    cmd = _command("wokwi-cli", project, timeout_ms=5000, serial_log=tmp_path / "serial.log")

    # --expect-text must NOT be passed when --scenario is used; otherwise wokwi-cli
    # exits immediately upon seeing the marker before scenario delays and screenshots run.
    assert "--expect-text" not in cmd
    assert "--scenario" in cmd
