from __future__ import annotations

from sparkplan.catalog import load_catalog
from sparkplan.firmware import generate_firmware
from sparkplan.geometry import compute_enclosure_plan
from sparkplan.guide import render_guide
from sparkplan.validators import run_validators
from tests.buildspecs import make_spec


def test_guide_lists_bom_and_wiring(catalog=None):
    catalog = catalog or load_catalog()
    spec = make_spec()
    guide = render_guide(
        spec,
        catalog,
        compute_enclosure_plan(spec, catalog),
        generate_firmware(spec, catalog),
        run_validators(spec, catalog),
    )
    assert "| env | DHT22" in guide
    assert "| screen | SSD1306" in guide
    assert "| DATA | GPIO4" in guide
    assert "| SDA | GPIO21" in guide
    assert "arduino-cli compile" in guide
    assert "M2.5" in guide


def test_guide_is_deterministic():
    catalog = load_catalog()
    spec = make_spec()
    args = (
        spec,
        catalog,
        compute_enclosure_plan(spec, catalog),
        generate_firmware(spec, catalog),
        run_validators(spec, catalog),
    )
    assert render_guide(*args) == render_guide(*args)


def test_guide_surfaces_warnings():
    catalog = load_catalog()
    spec = make_spec(
        connections=[
            {"instance_id": "env", "peripheral_pin": "DATA", "controller_pin": "GPIO0"},
            {"instance_id": "screen", "peripheral_pin": "SDA", "controller_pin": "GPIO21"},
            {"instance_id": "screen", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
        ]
    )
    guide = render_guide(spec, catalog, None, None, run_validators(spec, catalog))
    assert "GPIO_STRAP" in guide


def test_guide_without_enclosure_or_firmware():
    catalog = load_catalog()
    guide = render_guide(make_spec(), catalog, None, None, [])
    assert "Firmware not generated." in guide
    assert "## 3. Enclosure" not in guide
