"""Unit tests for automated PCB Gerber & Excellon Drill generation."""

import io
import zipfile
from pathlib import Path

from sparkplan.geometry.pcb_gerber import generate_pcb_gerbers
from sparkplan.models.buildspec import PartRef, SupportedBuild


def _sample_build() -> SupportedBuild:
    return SupportedBuild(
        status="ok",
        title="Test PCB Weather Station",
        summary="A test build for automated PCB Gerber generation.",
        parts=[
            PartRef(part_id="esp32_devkit_v1", instance_id="mcu"),
            PartRef(part_id="dht22", instance_id="temp_sensor"),
            PartRef(part_id="lcd1602_i2c", instance_id="screen"),
        ],
        connections=[],
    )


def test_generate_pcb_gerbers_in_memory() -> None:
    build = _sample_build()
    zip_bytes = generate_pcb_gerbers(build)
    assert len(zip_bytes) > 200

    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = set(zf.namelist())
    expected = {
        "board_outline.gko",
        "copper_top.gtl",
        "copper_bottom.gbl",
        "soldermask_top.gts",
        "soldermask_bottom.gbs",
        "silkscreen_top.gto",
        "drill.drl",
    }
    assert expected.issubset(names)

    # Check outline content
    outline = zf.read("board_outline.gko").decode("utf-8")
    assert "%MOMM*%" in outline
    assert "M02*" in outline

    # Check drill content
    drill = zf.read("drill.drl").decode("utf-8")
    assert "METRIC" in drill
    assert "T01C0.900" in drill
    assert "M30" in drill


def test_generate_pcb_gerbers_to_disk(tmp_path: Path) -> None:
    build = _sample_build()
    out_file = tmp_path / "gerbers.zip"
    returned_bytes = generate_pcb_gerbers(build, out_zip_path=out_file)

    assert out_file.exists()
    assert out_file.stat().st_size == len(returned_bytes)
