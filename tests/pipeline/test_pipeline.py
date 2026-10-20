from __future__ import annotations

import pytest
from sparkplan.catalog import load_catalog
from sparkplan.geometry import available_engines
from sparkplan.llm import FakeLLM
from sparkplan.pipeline import run_pipeline

VALID_SPEC = {
    "idea": "desk weather station",
    "controller": "esp32_devkit_v4",
    "parts": [
        {"instance_id": "env", "part_id": "dht22"},
        {"instance_id": "screen", "part_id": "ssd1306_oled"},
    ],
    "connections": [
        {"instance_id": "env", "peripheral_pin": "DATA", "controller_pin": "GPIO4"},
        {"instance_id": "screen", "peripheral_pin": "SDA", "controller_pin": "GPIO21"},
        {"instance_id": "screen", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
    ],
    "actions": [
        {
            "action_id": "read_temp",
            "kind": "read",
            "instance_id": "env",
            "api": "read_temperature_c",
        },
        {
            "action_id": "show",
            "kind": "write",
            "instance_id": "screen",
            "api": "print_line1",
            "consumes": "read_temp",
        },
    ],
}


@pytest.mark.skipif(not available_engines(), reason="no geometry engine")
def test_pipeline_ok_writes_artifacts(tmp_path):
    result = run_pipeline("desk weather station", FakeLLM(script=[VALID_SPEC]), out_dir=tmp_path)
    assert result.status == "ok"
    assert result.spec is not None
    assert result.enclosure is not None
    assert result.firmware is not None and "#include <DHT.h>" in result.firmware.sketch
    assert result.guide is not None and "Bill of materials" in result.guide
    assert (tmp_path / "enclosure_base.stl").exists()
    assert (tmp_path / "enclosure_lid.stl").exists()
    assert (tmp_path / "sparkplan_ino" / "sparkplan.ino").exists()
    assert (tmp_path / "BUILD_GUIDE.md").exists()
    assert (tmp_path / "buildspec.json").exists()


def test_pipeline_refusal():
    result = run_pipeline("build me a GPS drone with a camera", FakeLLM(script=[]))
    assert result.status == "refused"
    assert result.refusal_reasons
    assert result.enclosure is None


def test_pipeline_exhausted_is_failed():
    bad = dict(VALID_SPEC)
    bad["parts"] = [{"instance_id": "env", "part_id": "not_in_catalog"}]
    bad["connections"] = []
    bad["actions"] = []
    result = run_pipeline("weather station", FakeLLM(script=[bad] * 10))
    assert result.status == "failed"
    assert any(i.code == "UNKNOWN_PART" for i in result.issues)


def test_stages_usable_independently():
    catalog = load_catalog()
    from sparkplan.firmware import generate_firmware
    from sparkplan.geometry import build_enclosure, compute_enclosure_plan
    from sparkplan.guide import render_guide
    from sparkplan.models.spec import BuildSpec
    from sparkplan.validators import run_validators

    spec = BuildSpec.model_validate(VALID_SPEC)
    assert run_validators(spec, catalog) is not None
    plan = compute_enclosure_plan(spec, catalog)
    if available_engines():
        assert build_enclosure(plan).base_trimesh().volume > 0
    assert "DHT" in generate_firmware(spec, catalog).sketch
    assert render_guide(spec, catalog, plan, None, []).startswith("# Build guide")
