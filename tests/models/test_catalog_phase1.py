"""Phase 1 catalog tests: the new single-file catalog and its loader."""

from __future__ import annotations

from pathlib import Path

import pytest
from sparkplan.models.catalog import (
    CATALOG,
    CatalogError,
    Category,
    Interface,
    get_catalog,
    load_catalog,
    reset_catalog_cache,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "catalog_broken"

REQUIRED_IDS = {
    "esp32_devkit_30pin",
    "dht22",
    "soil_moisture_capacitive",
    "ssd1306_oled_096",
    "bme280",
    "bh1750",
    "hc_sr04",
    "voltage_divider_5v_3v3",
    "sg90_servo",
    "piezo_buzzer_passive",
    "led_5mm",
    "resistor_220r",
    "tactile_button",
    "pir_hc_sr501",
    "ws2812b_strip_8",
}


def test_catalog_imports_and_has_15_parts():
    assert len(CATALOG.parts) == 15


def test_all_required_parts_present_with_addresses():
    ids = {p.id for p in CATALOG.parts}
    assert ids == REQUIRED_IDS
    assert CATALOG.get("ssd1306_oled_096").i2c_address == "0x3C"
    assert CATALOG.get("bme280").i2c_address == "0x76"
    assert CATALOG.get("bh1750").i2c_address == "0x23"


def test_led_and_resistor_are_separate_entries():
    led = CATALOG.get("led_5mm")
    res = CATALOG.get("resistor_220r")
    assert led.category is Category.ACTUATOR
    assert res.category is Category.PASSIVE
    assert res.interface is Interface.NONE
    assert "resistor_220r" in led.notes


def test_board_part_has_board_model():
    board = CATALOG.get("esp32_devkit_30pin")
    assert board.category is Category.BOARD
    assert board.board is not None


def test_board_rails_and_budgets():
    board = CATALOG.get("esp32_devkit_30pin").board
    rails = {r.name: r for r in board.rails}
    assert set(rails) == {"3V3", "5V", "GND"}
    assert rails["3V3"].voltage_v == 3.3
    assert rails["3V3"].max_current_ma == 500
    assert rails["5V"].max_current_ma == 500
    assert all(r.approximate for r in board.rails)
    assert all(r.verify_against_datasheet for r in board.rails)
    assert all(r.source_note for r in board.rails)


def test_board_gpio_facts():
    board = CATALOG.get("esp32_devkit_30pin").board
    assert board.default_i2c_sda_gpio == 21
    assert board.default_i2c_scl_gpio == 22

    # input-only pins cannot drive outputs
    for gpio in (34, 35, 36, 39):
        pin = board.pin(gpio)
        assert pin is not None and pin.input_only and not pin.caps.output

    # flash-reserved pins are forbidden and have no peripheral caps
    for gpio in (6, 7, 8, 9, 10, 11):
        pin = board.pin(gpio)
        assert pin is not None and pin.forbidden

    # strapping pins
    for gpio in (0, 2, 12, 15):
        pin = board.pin(gpio)
        assert pin is not None and pin.strapping

    assert board.pin(21).default_i2c_sda
    assert board.pin(22).default_i2c_scl
    assert board.pin(32).caps.adc
    assert board.pin(25).caps.adc
    assert board.pin(23).caps.pwm
    assert board.pin(4).caps.pwm

    # every pin fact carries provenance
    for g in board.gpio:
        assert g.source_note
        assert g.verify_against_datasheet is True


def test_every_part_declares_unverified_dimensions():
    for part in CATALOG.parts:
        assert part.dimensions_verified is False


def test_catalog_lookup_helpers():
    assert CATALOG.get_or_none("nope") is None
    with pytest.raises(CatalogError):
        CATALOG.get("nope")
    assert CATALOG.board().id == "esp32_devkit_30pin"


@pytest.mark.parametrize(
    "fixture",
    [
        "unknown_field.json",
        "duplicate_id.json",
        "missing_pins.json",
        "bad_enum.json",
        "i2c_missing_address.json",
    ],
)
def test_broken_fixtures_are_rejected(fixture):
    with pytest.raises(CatalogError):
        load_catalog(FIXTURES / fixture)


def test_missing_file_raises():
    with pytest.raises(CatalogError):
        load_catalog(FIXTURES / "does_not_exist.json")


def test_loader_is_cached_and_resettable():
    reset_catalog_cache()
    first = get_catalog()
    second = get_catalog()
    assert first is second
    reset_catalog_cache()
    assert get_catalog() is not first


def _write_catalog(tmp_path, parts):
    import json

    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(parts), encoding="utf-8")
    return path


def _base_parts():
    import json
    from pathlib import Path

    catalog = json.loads(Path("catalog/parts.json").read_text())
    by_id = {part["id"]: part for part in catalog}
    return [by_id["esp32_devkit_30pin"], by_id["dht22"]]


def test_catalog_entries_declare_provenance_basis():
    for part in CATALOG.parts:
        assert part.dimensions_basis in {"manufacturer_spec", "measured", "estimate", "unknown"}
        assert part.dimensions_verified is False
    assert all(part.dimensions_basis == "estimate" for part in CATALOG.parts)
    assert all(part.dimensions_source is None for part in CATALOG.parts)


def test_catalog_loads_without_provenance_fields(tmp_path):
    parts = _base_parts()
    for part in parts:
        part.pop("dimensions_source", None)
        part.pop("dimensions_basis", None)
    loaded = load_catalog(_write_catalog(tmp_path, parts))
    assert len(loaded.parts) == 2
    assert all(part.dimensions_basis == "unknown" for part in loaded.parts)
    assert all(part.dimensions_source is None for part in loaded.parts)


def test_catalog_accepts_valid_provenance_values(tmp_path):
    parts = _base_parts()
    parts[1]["dimensions_source"] = "https://example.com/dht22-drawing"
    parts[1]["dimensions_basis"] = "manufacturer_spec"
    loaded = load_catalog(_write_catalog(tmp_path, parts))
    sensor = loaded.get("dht22")
    assert sensor.dimensions_source == "https://example.com/dht22-drawing"
    assert sensor.dimensions_basis == "manufacturer_spec"


def test_catalog_rejects_invalid_provenance_basis(tmp_path):
    parts = _base_parts()
    parts[1]["dimensions_basis"] = "datasheet_guess"
    with pytest.raises(CatalogError):
        load_catalog(_write_catalog(tmp_path, parts))


def test_catalog_rejects_manufacturer_basis_without_source(tmp_path):
    parts = _base_parts()
    parts[1]["dimensions_basis"] = "manufacturer_spec"
    parts[1]["dimensions_source"] = None
    with pytest.raises(CatalogError):
        load_catalog(_write_catalog(tmp_path, parts))


def test_catalog_provenance_round_trips():
    dumped = CATALOG.model_dump_json()
    from sparkplan.models.catalog import Catalog

    reloaded = Catalog.model_validate_json(dumped)
    assert reloaded == CATALOG
    assert all(part.dimensions_basis == "estimate" for part in reloaded.parts)


def test_estimated_dimensions_remain_unverified():
    assert all(part.dimensions_basis == "estimate" for part in CATALOG.parts)
    assert all(part.dimensions_verified is False for part in CATALOG.parts)
