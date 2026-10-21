"""Tests for the arduino-cli compile wrapper.

The wrapper must never claim a sketch compiled when it did not, and it must
say why it could not compile: no CLI, no ESP32 core, a missing library or a
compiler error.  The tests use a fake CLI and never touch the network.
"""

from __future__ import annotations

import os
import stat
import textwrap

from sparkplan.firmware.compile import compile_sketch
from sparkplan.firmware.generate import FirmwareResult, compile_firmware


def _fake_cli(tmp_path, body: str) -> str:
    path = tmp_path / "arduino-cli"
    path.write_text(textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _sketch_dir(tmp_path):
    sketch_dir = tmp_path / "sketch"
    sketch_dir.mkdir()
    (sketch_dir / "sketch.ino").write_text("void setup() {}\nvoid loop() {}\n")
    return sketch_dir


def test_missing_cli_is_skipped(tmp_path):
    artifacts = compile_sketch(_sketch_dir(tmp_path), cli_path=str(tmp_path / "nope"))
    assert artifacts.status == "skipped"
    assert artifacts.issues[0].code == "ARDUINO_CLI_MISSING"
    assert artifacts.missing == [f"arduino-cli ({tmp_path / 'nope'})"]


def test_missing_core_is_skipped(tmp_path):
    cli = _fake_cli(
        tmp_path,
        """\
        #!/bin/sh
        echo "Platform 'esp32:esp32' not found" >&2
        exit 1
        """,
    )
    artifacts = compile_sketch(_sketch_dir(tmp_path), cli_path=cli)
    assert artifacts.status == "skipped"
    assert artifacts.issues[0].code == "ESP32_PLATFORM_MISSING"
    assert artifacts.missing == ["platform:esp32:esp32"]


def test_compiler_error_is_reported(tmp_path):
    cli = _fake_cli(
        tmp_path,
        """\
        #!/bin/sh
        echo "sketch.ino:12: error: expected ';' before '}' token" >&2
        exit 1
        """,
    )
    artifacts = compile_sketch(_sketch_dir(tmp_path), cli_path=cli)
    assert artifacts.status == "failed"
    assert artifacts.exit_code == 1
    assert artifacts.issues[0].code == "COMPILE_ERROR"
    assert "expected ';'" in artifacts.issues[0].message


def test_successful_compile_is_reported(tmp_path):
    cli = _fake_cli(
        tmp_path,
        """\
        #!/bin/sh
        echo "Sketch uses 314016 bytes (23%) of program storage space."
        exit 0
        """,
    )
    artifacts = compile_sketch(_sketch_dir(tmp_path), cli_path=cli)
    assert artifacts.status == "passed"
    assert artifacts.issues == []
    assert "Sketch uses" in artifacts.logs


def test_build_properties_are_passed_to_the_cli(tmp_path):
    log = tmp_path / "cli.log"
    cli = _fake_cli(
        tmp_path,
        f"""\
        #!/bin/sh
        echo "$@" >> {log}
        exit 0
        """,
    )
    artifacts = compile_sketch(
        _sketch_dir(tmp_path),
        cli_path=cli,
        build_properties=["tools.ctags.pattern=/usr/bin/true"],
    )
    assert artifacts.status == "passed"
    assert "--build-property tools.ctags.pattern=/usr/bin/true" in log.read_text()


def test_build_properties_can_come_from_the_environment(tmp_path, monkeypatch):
    log = tmp_path / "cli.log"
    cli = _fake_cli(
        tmp_path,
        f"""\
        #!/bin/sh
        echo "$@" >> {log}
        exit 0
        """,
    )
    monkeypatch.setenv("SPARKPLAN_ARDUINO_BUILD_PROPERTIES", "tools.ctags.pattern=/usr/bin/true")
    artifacts = compile_sketch(_sketch_dir(tmp_path), cli_path=cli)
    assert artifacts.status == "passed"
    assert "--build-property tools.ctags.pattern=/usr/bin/true" in log.read_text()


def test_libraries_are_installed_with_their_pinned_version(tmp_path):
    log = tmp_path / "cli.log"
    cli = _fake_cli(
        tmp_path,
        f"""\
        #!/bin/sh
        echo "$@" >> {log}
        exit 0
        """,
    )
    artifacts = compile_sketch(
        _sketch_dir(tmp_path),
        cli_path=cli,
        libraries=["DHT sensor library@1.4.7"],
    )
    assert artifacts.status == "passed"
    assert "lib install DHT sensor library@1.4.7" in log.read_text()


def test_missing_library_is_skipped(tmp_path):
    cli = _fake_cli(
        tmp_path,
        """\
        #!/bin/sh
        echo "Library 'Nope' not found in the library index" >&2
        exit 1
        """,
    )
    artifacts = compile_sketch(
        _sketch_dir(tmp_path),
        cli_path=cli,
        libraries=["Nope@1.0.0"],
    )
    assert artifacts.status == "skipped"
    assert artifacts.issues[0].code == "LIBRARY_UNAVAILABLE"


def test_compile_firmware_uses_the_pinned_manifest(tmp_path, monkeypatch):
    log = tmp_path / "cli.log"
    cli = _fake_cli(
        tmp_path,
        f"""\
        #!/bin/sh
        echo "$@" >> {log}
        exit 0
        """,
    )
    monkeypatch.setenv("SPARKPLAN_ARDUINO_CLI", cli)
    result = FirmwareResult(
        sketch="void setup() {}\nvoid loop() {}\n", libraries=["DHT sensor library"]
    )
    compiled = compile_firmware(result, tmp_path / "ino")
    assert compiled.compile_status == "ok"
    install = log.read_text().splitlines()[0]
    assert install.startswith("lib install ")
    assert "DHT sensor library@1.4.7" in install
    assert "Adafruit Unified Sensor@1.1.15" in install
    libraries_txt = (tmp_path / "ino" / "libraries.txt").read_text()
    assert "DHT sensor library@1.4.7" in libraries_txt
    assert "Adafruit Unified Sensor@1.1.15" in libraries_txt


def test_compile_firmware_refuses_an_unpinned_library(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARKPLAN_ARDUINO_CLI", str(tmp_path / "missing-cli"))
    result = FirmwareResult(sketch="void setup() {}\n", libraries=["Not A Library"])
    compiled = compile_firmware(result, tmp_path / "ino")
    assert compiled.compile_status == "skipped"
    assert "FIRMWARE_UNPINNED_LIBRARY" in {issue.code for issue in compiled.issues}


def test_compile_firmware_of_a_sketch_without_libraries(tmp_path, monkeypatch):
    """A sketch that needs no library must not claim the whole registry."""
    log = tmp_path / "cli.log"
    cli = _fake_cli(
        tmp_path,
        f"""\
        #!/bin/sh
        echo "$@" >> {log}
        exit 0
        """,
    )
    monkeypatch.setenv("SPARKPLAN_ARDUINO_CLI", cli)
    result = FirmwareResult(sketch="void setup() {}\nvoid loop() {}\n", libraries=[])
    compiled = compile_firmware(result, tmp_path / "ino")
    assert compiled.compile_status == "ok"
    assert (tmp_path / "ino" / "libraries.txt").read_text() == ""
    assert "lib install" not in log.read_text()


def test_build_properties_environment_is_not_leaked(monkeypatch):
    monkeypatch.delenv("SPARKPLAN_ARDUINO_BUILD_PROPERTIES", raising=False)
    assert "SPARKPLAN_ARDUINO_BUILD_PROPERTIES" not in os.environ
