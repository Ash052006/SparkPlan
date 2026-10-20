from __future__ import annotations

import pytest
from sparkplan.catalog import CatalogError, load_catalog


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def test_catalog_has_expected_part_count(catalog):
    assert 15 <= len(catalog.parts) <= 25


def test_controller_present(catalog):
    c = catalog.controller
    assert c.part_id == "esp32_devkit_v4"
    assert len(c.pins) >= 30


def test_every_part_valid_and_addressable(catalog):
    for part_id, part in catalog.parts.items():
        assert part.part_id == part_id
        assert part.pins, part_id
        assert part.mechanical.length_mm > 0
        assert part.mechanical.width_mm > 0


def test_every_signal_part_has_firmware_template(catalog):
    for part in catalog.parts.values():
        if part.category.value == "power" or part.part_id == "esp32_devkit_v4":
            continue
        assert part.firmware.api, part.part_id
        for fn in part.firmware.api:
            assert "{instance}" in fn.fn, f"{part.part_id}:{fn.name}"


def test_i2c_parts_declare_addresses(catalog):
    expected = {"mpu6050": "0x68", "bmp280": "0x76", "ssd1306_oled": "0x3C"}
    for pid, addr in expected.items():
        part = catalog.part(pid)
        assert part.firmware.i2c_address == addr, pid
        assert any(addr in d for d in part.firmware.defines), pid


def test_power_model_rails(catalog):
    assert catalog.power.rails["3v3"].max_ma == 500
    assert catalog.power.rails["5v"].max_ma == 500
    assert catalog.power.rails["5v"].controller_share_ma > 0


def test_gpio_table_capabilities(catalog):
    assert catalog.gpios.can_output(4)
    assert not catalog.gpios.can_output(34)  # input only
    assert not catalog.gpios.can_output(1)  # uart0 tx reserved
    assert catalog.gpios.can_input(34)
    assert catalog.gpios.can_analog(32)
    assert not catalog.gpios.can_analog(23)
    assert catalog.gpios.has_cap(21, "i2c_sda")
    assert catalog.gpios.has_cap(22, "i2c_scl")
    assert not catalog.gpios.is_free(1) and not catalog.gpios.is_free(3)


def test_capabilities_index_covers_all_non_power_parts(catalog):
    referenced: set[str] = set()
    for cap in catalog.capabilities.capabilities:
        referenced.update(cap.parts)
    for part in catalog.parts.values():
        if part.category.value in ("power", "controller"):
            continue
        assert part.part_id in referenced, f"{part.part_id} not reachable from any capability"


def test_missing_part_raises():
    cat = load_catalog()
    with pytest.raises(CatalogError):
        cat.part("definitely_not_a_part")
