"""Automated standard 2-layer PCB Gerber & Excellon Drill generator.

Generates industry-standard RS-274X Gerber and Excellon drill files
compatible with JLCPCB, PCBWay, and OSH Park from a ``SupportedBuild`` spec.
All units: millimetres.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

from sparkplan.models.buildspec import SupportedBuild
from sparkplan.models.catalog import Catalog, get_catalog


@dataclass(frozen=True)
class Pad:
    """Through-hole or SMD pad."""

    x: float
    y: float
    drill_mm: float
    diameter_mm: float
    net: str
    pin_name: str
    component: str


@dataclass(frozen=True)
class TraceSegment:
    """Copper track segment between two coordinates."""

    x1: float
    y1: float
    x2: float
    y2: float
    width_mm: float
    net: str
    layer: str  # "top" or "bottom"


@dataclass(frozen=True)
class SilkLabel:
    """Silkscreen label."""

    x: float
    y: float
    text: str
    size_mm: float = 1.2


def _fmt_coord(mm: float) -> str:
    """Format millimetres into 4.4 integer coordinate string (units: 0.0001 mm)."""
    val = int(round(mm * 10000.0))
    if val >= 0:
        return f"{val:08d}"
    return f"-{abs(val):07d}"


def _fmt_drill_coord(mm: float) -> str:
    """Format millimetres into Excellon 3.3 coordinate (units: 0.001 mm)."""
    val = int(round(mm * 1000.0))
    if val >= 0:
        return f"X{val:06d}"
    return f"X-{abs(val):05d}"


def _generate_board_outline_gko(width: float, height: float) -> str:
    """Generate GKO (Edge.Cuts) Gerber for rectangular PCB with rounded corners."""
    r = 2.0  # corner radius mm
    out = [
        "G04 SparkPlan Autonomous Hardware Studio Edge.Cuts*",
        "%FSLAX24Y24*%",
        "%MOMM*%",
        "%LPD*%",
        "%ADD10C,0.1500*%",  # 0.15mm outline width
        "D10*",
        "G01*",
        # Start bottom left after corner
        f"X{_fmt_coord(r)}Y{_fmt_coord(0.0)}D02*",
        f"X{_fmt_coord(width - r)}Y{_fmt_coord(0.0)}D01*",
        f"X{_fmt_coord(width)}Y{_fmt_coord(r)}D01*",
        f"X{_fmt_coord(width)}Y{_fmt_coord(height - r)}D01*",
        f"X{_fmt_coord(width - r)}Y{_fmt_coord(height)}D01*",
        f"X{_fmt_coord(r)}Y{_fmt_coord(height)}D01*",
        f"X{_fmt_coord(0.0)}Y{_fmt_coord(height - r)}D01*",
        f"X{_fmt_coord(0.0)}Y{_fmt_coord(r)}D01*",
        f"X{_fmt_coord(r)}Y{_fmt_coord(0.0)}D01*",
        "M02*",
    ]
    return "\n".join(out) + "\n"


def _generate_copper_gerber(
    layer_name: str,
    pads: list[Pad],
    traces: list[TraceSegment],
    layer_filter: str,
) -> str:
    """Generate RS-274X Top or Bottom Copper Gerber."""
    out = [
        f"G04 SparkPlan Copper Layer {layer_name}*",
        "%FSLAX24Y24*%",
        "%MOMM*%",
        "%LPD*%",
        "%ADD10C,1.6000*%",  # Standard pin header pad
        "%ADD11C,2.2000*%",  # Power / Ground pad
        "%ADD12C,0.2540*%",  # Signal trace width 10 mil
        "%ADD13C,0.5080*%",  # Power trace width 20 mil
        "G01*",
    ]

    # Flash pads
    out.append("D10*")
    for pad in pads:
        aperture = "D11*" if "GND" in pad.net or "VCC" in pad.net or "5V" in pad.net else "D10*"
        out.append(f"{aperture}")
        out.append(f"X{_fmt_coord(pad.x)}Y{_fmt_coord(pad.y)}D03*")

    # Draw traces
    for tr in traces:
        if tr.layer != layer_filter:
            continue
        width_code = "D13*" if tr.width_mm > 0.3 else "D12*"
        out.append(width_code)
        out.append(f"X{_fmt_coord(tr.x1)}Y{_fmt_coord(tr.y1)}D02*")
        out.append(f"X{_fmt_coord(tr.x2)}Y{_fmt_coord(tr.y2)}D01*")

    out.append("M02*")
    return "\n".join(out) + "\n"


def _generate_soldermask_gerber(pads: list[Pad], layer_name: str) -> str:
    """Generate Solder Mask Gerber (clearance around all pads)."""
    out = [
        f"G04 SparkPlan Solder Mask {layer_name}*",
        "%FSLAX24Y24*%",
        "%MOMM*%",
        "%LPD*%",
        "%ADD10C,1.8000*%",  # 0.2mm mask expansion
        "%ADD11C,2.4000*%",
        "G01*",
    ]
    for pad in pads:
        aperture = "D11*" if "GND" in pad.net or "VCC" in pad.net else "D10*"
        out.append(f"{aperture}")
        out.append(f"X{_fmt_coord(pad.x)}Y{_fmt_coord(pad.y)}D03*")
    out.append("M02*")
    return "\n".join(out) + "\n"


def _generate_silkscreen_gerber(
    width: float,
    height: float,
    labels: list[SilkLabel],
    pads: list[Pad],
) -> str:
    """Generate Top Silkscreen with component reference designators and pin markings."""
    out = [
        "G04 SparkPlan Silkscreen Top*",
        "%FSLAX24Y24*%",
        "%MOMM*%",
        "%LPD*%",
        "%ADD10C,0.1500*%",  # Silkscreen stroke width
        "D10*",
        "G01*",
    ]
    # Board branding
    labels_to_draw = [
        SilkLabel(x=width / 2.0, y=height - 3.5, text="SparkPlan AI PCB"),
        SilkLabel(x=width / 2.0, y=3.0, text="ESP32-WROOM-32 DevKit"),
        *labels,
    ]
    for lbl in labels_to_draw:
        # Simple stroke simulation for text anchor line
        lx = lbl.x - (len(lbl.text) * 0.45)
        out.append(f"X{_fmt_coord(lx)}Y{_fmt_coord(lbl.y)}D02*")
        out.append(f"X{_fmt_coord(lx + len(lbl.text) * 0.9)}Y{_fmt_coord(lbl.y)}D01*")

    # Draw pin 1 square indicator for ESP32
    if pads:
        p1 = pads[0]
        s = 0.8
        out.append(f"X{_fmt_coord(p1.x - s)}Y{_fmt_coord(p1.y - s)}D02*")
        out.append(f"X{_fmt_coord(p1.x + s)}Y{_fmt_coord(p1.y - s)}D01*")
        out.append(f"X{_fmt_coord(p1.x + s)}Y{_fmt_coord(p1.y + s)}D01*")
        out.append(f"X{_fmt_coord(p1.x - s)}Y{_fmt_coord(p1.y + s)}D01*")
        out.append(f"X{_fmt_coord(p1.x - s)}Y{_fmt_coord(p1.y - s)}D01*")

    out.append("M02*")
    return "\n".join(out) + "\n"


def _generate_excellon_drill(pads: list[Pad], width: float, height: float) -> str:
    """Generate Excellon drill file with header pins and M3 corner mounting holes."""
    out = [
        "M48",
        "; Layer: Excellon Drill",
        "METRIC,LZ",
        "T01C0.900",  # Standard 2.54mm pin header drill
        "T02C3.200",  # M3 mounting hole
        "%",
        "G90",
        "G05",
        "T01",
    ]
    for pad in pads:
        out.append(f"{_fmt_drill_coord(pad.x)}Y{int(round(pad.y * 1000.0)):06d}")

    # 4 M3 mounting holes in corners (3mm inset)
    out.append("T02")
    inset = 3.5
    for mx, my in (
        (inset, inset),
        (width - inset, inset),
        (width - inset, height - inset),
        (inset, height - inset),
    ):
        out.append(f"{_fmt_drill_coord(mx)}Y{int(round(my * 1000.0)):06d}")

    out.append("M30")
    return "\n".join(out) + "\n"


def generate_pcb_gerbers(
    spec: SupportedBuild,
    catalog: Catalog | None = None,
    out_zip_path: str | Path | None = None,
) -> bytes:
    """Synthesize complete RS-274X Gerber & Excellon Drill files into a zip archive.

    If ``out_zip_path`` is provided, writes the archive to disk and returns the raw bytes.
    """
    cat = catalog or get_catalog()
    board_width = 80.0  # mm
    board_height = 65.0  # mm

    pads: list[Pad] = []
    traces: list[TraceSegment] = []
    labels: list[SilkLabel] = []

    # 1. Place ESP32 DevKit V1 headers (2 rows of 15 pins, 25.4mm row spacing, 2.54mm pitch)
    esp32_x_left = 18.0
    esp32_x_right = esp32_x_left + 25.4
    esp32_y_start = 14.0
    pitch = 2.54

    esp32_left_pins = [
        "EN",
        "VP",
        "VN",
        "D34",
        "D35",
        "D32",
        "D33",
        "D25",
        "D26",
        "D27",
        "D14",
        "D12",
        "D13",
        "GND",
        "VIN",
    ]
    esp32_right_pins = [
        "D23",
        "D22",
        "TX0",
        "RX0",
        "D21",
        "D19",
        "D18",
        "D5",
        "TX2",
        "RX2",
        "D4",
        "D2",
        "D15",
        "GND",
        "3V3",
    ]

    esp32_pad_map: dict[str, Pad] = {}
    for i, pin in enumerate(esp32_left_pins):
        py = esp32_y_start + i * pitch
        pad = Pad(
            x=esp32_x_left,
            y=py,
            drill_mm=0.9,
            diameter_mm=1.6,
            net=pin,
            pin_name=pin,
            component="ESP32_DevKit",
        )
        pads.append(pad)
        esp32_pad_map[pin] = pad

    for i, pin in enumerate(esp32_right_pins):
        py = esp32_y_start + (14 - i) * pitch
        pad = Pad(
            x=esp32_x_right,
            y=py,
            drill_mm=0.9,
            diameter_mm=1.6,
            net=pin,
            pin_name=pin,
            component="ESP32_DevKit",
        )
        pads.append(pad)
        esp32_pad_map[pin] = pad

    # 2. Place peripheral sensor / module pin headers on the right side of the PCB
    peripheral_x = 58.0
    curr_y = 14.0

    for part_inst in spec.parts:
        part_meta = cat.get_or_none(part_inst.part_id)
        if not part_meta:
            continue
        labels.append(SilkLabel(x=peripheral_x, y=curr_y - 1.2, text=part_inst.instance_id.upper()))
        # Add 3 or 4 pin header for this module (GND, VCC/5V/3V3, Signal/SDA, SCL)
        pins = (
            ["GND", "VCC", "DATA"]
            if "dht" in part_inst.part_id
            else (
                ["GND", "VCC", "SDA", "SCL"]
                if part_meta.has_capability("i2c")
                else ["GND", "VCC", "SIG"]
            )
        )
        for p_idx, p_name in enumerate(pins):
            pad_y = curr_y + p_idx * pitch
            module_pad = Pad(
                x=peripheral_x,
                y=pad_y,
                drill_mm=0.9,
                diameter_mm=1.6,
                net=f"{part_inst.instance_id}_{p_name}",
                pin_name=p_name,
                component=part_inst.instance_id,
            )
            pads.append(module_pad)

            # Route track from peripheral pad to ESP32 connection
            # Route ground / power or signal trace
            if p_name == "GND":
                target_pin = "GND"
            elif p_name == "VCC":
                target_pin = "3V3"
            elif p_name == "SDA":
                target_pin = "D21"
            elif p_name == "SCL":
                target_pin = "D22"
            else:
                target_pin = "D4"

            if target_pin in esp32_pad_map:
                esp_pad = esp32_pad_map[target_pin]
                # Manhattan 2-segment route
                mid_x = (module_pad.x + esp_pad.x) / 2.0
                layer = "top" if p_name != "GND" else "bottom"
                width = 0.508 if "GND" in p_name or "VCC" in p_name else 0.254
                traces.append(
                    TraceSegment(
                        x1=module_pad.x,
                        y1=module_pad.y,
                        x2=mid_x,
                        y2=module_pad.y,
                        width_mm=width,
                        net=module_pad.net,
                        layer=layer,
                    )
                )
                traces.append(
                    TraceSegment(
                        x1=mid_x,
                        y1=module_pad.y,
                        x2=esp_pad.x,
                        y2=esp_pad.y,
                        width_mm=width,
                        net=module_pad.net,
                        layer=layer,
                    )
                )

        curr_y += len(pins) * pitch + 5.0

    # 3. Generate file payloads
    files: dict[str, str] = {
        "board_outline.gko": _generate_board_outline_gko(board_width, board_height),
        "copper_top.gtl": _generate_copper_gerber("Top", pads, traces, "top"),
        "copper_bottom.gbl": _generate_copper_gerber("Bottom", pads, traces, "bottom"),
        "soldermask_top.gts": _generate_soldermask_gerber(pads, "Top"),
        "soldermask_bottom.gbs": _generate_soldermask_gerber(pads, "Bottom"),
        "silkscreen_top.gto": _generate_silkscreen_gerber(board_width, board_height, labels, pads),
        "drill.drl": _generate_excellon_drill(pads, board_width, board_height),
    }

    # 4. Zip files in memory
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fname, content in files.items():
            zf.writestr(fname, content)

    zip_bytes = buf.getvalue()

    if out_zip_path is not None:
        out_path = Path(out_zip_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(zip_bytes)

    return zip_bytes
