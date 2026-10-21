"""Tests for the pinned library manifest.

The manifest is what makes a build reproducible: every library is pinned to
one version, every pin carries its download URL and checksum, and a library
that is not pinned is reported instead of resolved to "latest".
"""

from __future__ import annotations

import json

from sparkplan.firmware.manifest import (
    LIBRARY_INDEX_URL,
    PINNED_LIBRARIES,
    libraries_txt,
    pinned_spec,
    resolve_libraries,
    write_manifest,
)
from sparkplan.models.catalog import load_catalog


def test_every_catalog_library_is_pinned():
    catalog = load_catalog()
    missing: list[str] = []
    for part in catalog.parts:
        for library in part.firmware.library_deps:
            if library not in PINNED_LIBRARIES:
                missing.append(f"{part.id}:{library}")
    assert not missing, f"catalog libraries without a pin: {missing}"


def test_every_pin_has_a_source_url_and_a_checksum():
    for name, entry in PINNED_LIBRARIES.items():
        assert entry.source_url.startswith("https://downloads.arduino.cc/libraries/"), name
        assert entry.checksum.startswith("SHA-256:"), name
        assert len(entry.checksum) == len("SHA-256:") + 64, name
        assert entry.repository.startswith("https://"), name


def test_pinned_spec_is_name_and_version():
    assert pinned_spec("DHT sensor library") == "DHT sensor library@1.4.7"


def test_transitive_dependencies_are_resolved():
    resolution = resolve_libraries(["Adafruit SSD1306"])
    assert resolution.ok
    assert resolution.specs == (
        "Adafruit BusIO@1.17.4",
        "Adafruit GFX Library@1.13.0",
        "Adafruit SSD1306@2.5.17",
    )


def test_resolution_is_sorted_and_deterministic():
    first = resolve_libraries(["DHT sensor library", "Adafruit SSD1306"])
    second = resolve_libraries(["Adafruit SSD1306", "DHT sensor library"])
    assert first.specs == second.specs


def test_unknown_library_is_reported():
    resolution = resolve_libraries(["DHT sensor library", "Not A Library"])
    assert not resolution.ok
    assert resolution.unknown == ("Not A Library",)
    assert "DHT sensor library@1.4.7" in resolution.specs


def test_libraries_txt_lists_pinned_versions():
    text = libraries_txt(["DHT sensor library"])
    assert text == "Adafruit Unified Sensor@1.1.15\nDHT sensor library@1.4.7\n"


def test_write_manifest_writes_both_files(tmp_path):
    txt, js = write_manifest(tmp_path, ["DHT sensor library"])
    assert txt.name == "libraries.txt"
    assert js.name == "library_manifest.json"
    assert "DHT sensor library@1.4.7" in txt.read_text()
    data = json.loads(js.read_text())
    assert data["index"] == LIBRARY_INDEX_URL
    entry = next(e for e in data["libraries"] if e["arduino_name"] == "DHT sensor library")
    assert entry["pinned_version"] == "1.4.7"
    assert entry["checksum"].startswith("SHA-256:")


def test_write_manifest_of_a_sketch_without_libraries_is_empty(tmp_path):
    """A sketch that needs no library must not claim the whole registry."""
    txt, js = write_manifest(tmp_path, [])
    assert txt.read_text() == ""
    assert json.loads(js.read_text())["libraries"] == []


def test_write_manifest_without_names_writes_the_whole_registry(tmp_path):
    txt, _ = write_manifest(tmp_path)
    assert len(txt.read_text().splitlines()) == len(PINNED_LIBRARIES)
