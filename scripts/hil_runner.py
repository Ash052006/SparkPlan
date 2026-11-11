#!/usr/bin/env python3
"""Hardware-in-the-Loop (HIL) Silicon Test Rig Runner.

Automates the flashing and live physical validation of compiled SparkPlan
ESP32 binaries on attached hardware test rigs.

Supports:
- Auto-detecting USB serial ports (CP2102, CH340, FTDI, ESP32-S3 native USB)
- Flashing firmware via esptool
- Reading serial boot output at 115200 baud
- Asserting bootloader banner and sensor polling telemetry
- Offline / CI mock mode (--mock) for running without physical USB boards
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Final

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("hil_runner")

COMMON_PORT_PATTERNS: Final[tuple[str, ...]] = (
    "/dev/ttyUSB*",
    "/dev/ttyACM*",
    "/dev/tty.usbserial*",
    "/dev/tty.SLAB_USBtoUART*",
    "/dev/cu.usbserial*",
    "/dev/cu.SLAB_USBtoUART*",
    "/dev/cu.wchusbserial*",
    "COM[0-9]*",
)

MOCK_BOOT_OUTPUT: Final[str] = """
rst:0x1 (POWERON_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT)
configsip: 0, SPIWP:0xee
clk_drv:0x00,q_drv:0x00,d_drv:0x00,cs0_drv:0x00,hd_drv:0x00,wp_drv:0x00
mode:DIO, clock div:1
load:0x3fff0030,len:1184
load:0x40078000,len:13260
load:0x40080400,len:3028
entry 0x400805e4
[SPARKPLAN_INIT] ESP32-WROOM-32 Booting SparkPlan Generated Firmware...
[SPARKPLAN_SENSORS] Initializing I2C bus SDA=21 SCL=22... OK
[SPARKPLAN_TELEMETRY] Sensor poll cycle 1: temperature=24.5C humidity=48.2%
[SPARKPLAN_LOOP] Heartbeat active.
"""


@dataclass
class HILResult:
    passed: bool
    port: str
    boot_banner_found: bool
    telemetry_found: bool
    elapsed_s: float
    output: str
    error: str | None = None


def find_serial_ports() -> list[str]:
    """Discover available USB serial ports matching ESP32 devkits."""
    ports: list[str] = []
    for pattern in COMMON_PORT_PATTERNS:
        ports.extend(glob.glob(pattern))
    return sorted(set(ports))


def flash_esp32_binary(bin_path: str, port: str, baud: int = 460800, mock: bool = False) -> bool:
    """Flash firmware binary using esptool."""
    if mock:
        logger.info("[MOCK] Flashing %s to %s at %d baud (simulated)", bin_path, port, baud)
        time.sleep(0.1)
        return True

    esptool = shutil.which("esptool.py") or shutil.which("esptool")
    if not esptool:
        logger.error("esptool not found in PATH. Install via pip install esptool.")
        return False

    cmd = [
        esptool,
        "--chip",
        "esp32",
        "--port",
        port,
        "--baud",
        str(baud),
        "write_flash",
        "-z",
        "0x10000",
        bin_path,
    ]
    logger.info("Executing flash command: %s", " ".join(cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
        if proc.returncode != 0:
            logger.error("esptool failed:\n%s", proc.stderr or proc.stdout)
            return False
        logger.info("Flash completed successfully.")
        return True
    except Exception as err:
        logger.exception("Error during flashing: %s", err)
        return False


def verify_telemetry(
    port: str,
    timeout_s: float = 10.0,
    baud: int = 115200,
    mock: bool = False,
) -> HILResult:
    """Read UART serial output and assert expected boot sequence and telemetry."""
    start_time = time.monotonic()

    if mock:
        logger.info("[MOCK] Reading simulated UART serial from %s at %d baud...", port, baud)
        time.sleep(0.15)
        raw_output = MOCK_BOOT_OUTPUT
    else:
        try:
            import serial  # type: ignore[import-untyped]
        except ImportError:
            return HILResult(
                passed=False,
                port=port,
                boot_banner_found=False,
                telemetry_found=False,
                elapsed_s=0.0,
                output="",
                error="pyserial is not installed. Install via pip install pyserial.",
            )

        try:
            ser = serial.Serial(port, baud, timeout=1.0)
            # Reset board via DTR/RTS toggle
            ser.setDTR(False)
            ser.setRTS(True)
            time.sleep(0.1)
            ser.setDTR(True)
            ser.setRTS(False)
            time.sleep(0.2)

            lines: list[str] = []
            deadline = start_time + timeout_s
            while time.monotonic() < deadline:
                line = ser.readline().decode("utf-8", errors="replace")
                if line:
                    lines.append(line)
                    if "[SPARKPLAN_LOOP]" in line or "[SPARKPLAN_TELEMETRY]" in line:
                        break
            ser.close()
            raw_output = "".join(lines)
        except Exception as err:
            return HILResult(
                passed=False,
                port=port,
                boot_banner_found=False,
                telemetry_found=False,
                elapsed_s=time.monotonic() - start_time,
                output="",
                error=str(err),
            )

    elapsed = time.monotonic() - start_time
    boot_found = "POWERON_RESET" in raw_output or "SPARKPLAN_INIT" in raw_output
    telemetry_found = "SPARKPLAN_TELEMETRY" in raw_output or "temperature" in raw_output.lower()

    passed = boot_found and telemetry_found
    return HILResult(
        passed=passed,
        port=port,
        boot_banner_found=boot_found,
        telemetry_found=telemetry_found,
        elapsed_s=elapsed,
        output=raw_output,
    )


def run_hil_test(bin_path: str, port: str | None = None, mock: bool = False) -> int:
    """Execute complete HIL cycle: detect port -> flash -> verify serial."""
    target_port = port
    if not target_port:
        if mock:
            target_port = "/dev/ttyUSB0 (MOCK_ESP32_RIG)"
        else:
            ports = find_serial_ports()
            if not ports:
                logger.error("No USB serial devices detected. Connect an ESP32 or use --mock.")
                return 1
            target_port = ports[0]
            logger.info("Auto-detected ESP32 on port: %s", target_port)

    logger.info("=== Starting SparkPlan HIL Silicon Test ===")
    logger.info("Target Port: %s", target_port)
    logger.info("Firmware Binary: %s", bin_path)

    # 1. Flash binary
    flash_ok = flash_esp32_binary(bin_path, target_port, mock=mock)
    if not flash_ok:
        logger.error("HIL Flashing stage failed.")
        return 2

    # 2. Verify serial boot telemetry
    result = verify_telemetry(target_port, timeout_s=10.0, mock=mock)
    if not result.passed:
        logger.error("HIL Telemetry verification failed.")
        if result.error:
            logger.error("Error: %s", result.error)
        logger.error("Captured Serial Output:\n%s", result.output)
        return 3

    logger.info("=== HIL Silicon Test PASSED in %.2fs ===", result.elapsed_s)
    logger.info("Boot banner verified: %s", result.boot_banner_found)
    logger.info("Telemetry verified: %s", result.telemetry_found)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="SparkPlan Hardware-in-the-Loop Runner")
    parser.add_argument("--bin", default="tests/fixtures/firmware.bin", help="Path to firmware.bin")
    parser.add_argument("--port", default=None, help="Serial port (e.g. /dev/ttyUSB0)")
    parser.add_argument(
        "--mock", action="store_true", help="Run simulated HIL cycle without physical rig"
    )
    args = parser.parse_args()

    # Create dummy binary if running in mock mode and file doesn't exist
    if args.mock and not os.path.exists(args.bin):
        os.makedirs(os.path.dirname(args.bin) or ".", exist_ok=True)
        with open(args.bin, "wb") as f:
            f.write(b"MOCK_SPARKPLAN_ESP32_FIRMWARE_BINARY")

    sys.exit(run_hil_test(args.bin, args.port, mock=args.mock))


if __name__ == "__main__":
    main()
