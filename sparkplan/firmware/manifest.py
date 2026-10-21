"""Pinned Arduino library manifest for Phase 5.

Every library a SparkPlan sketch can need is pinned to one exact version.
The pin comes from the official Arduino library index
(``https://downloads.arduino.cc/libraries/library_index.json``), and the
download URL and SHA-256 checksum of every pinned version are recorded here
so a build can be reproduced and audited:

* the version and the checksum come from the Arduino library index;
* the transitive dependencies come from the ``depends`` field of the
  installed ``library.properties`` of that exact version.

Nothing in this module resolves a floating version.  A library that is not
pinned here is an error, never a silent "latest": a sketch that compiles
today must compile the same way tomorrow.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

__all__ = [
    "LIBRARY_INDEX_URL",
    "LibraryEntry",
    "LibraryResolution",
    "PINNED_LIBRARIES",
    "libraries_txt",
    "pinned_spec",
    "resolve_libraries",
    "write_manifest",
]

# The authoritative index every pin below was taken from.
LIBRARY_INDEX_URL = "https://downloads.arduino.cc/libraries/library_index.json"


@dataclass(frozen=True)
class LibraryEntry:
    """One pinned Arduino library."""

    arduino_name: str
    pinned_version: str
    source_url: str
    checksum: str
    repository: str
    transitive_deps: tuple[str, ...] = ()
    notes: str = ""

    @property
    def spec(self) -> str:
        """The ``Name@version`` string arduino-cli understands."""
        return f"{self.arduino_name}@{self.pinned_version}"


@dataclass(frozen=True)
class LibraryResolution:
    """The outcome of resolving catalog library names against the manifest."""

    entries: tuple[LibraryEntry, ...] = ()
    unknown: tuple[str, ...] = ()
    requested: tuple[str, ...] = field(default_factory=tuple)

    @property
    def specs(self) -> tuple[str, ...]:
        """Pinned ``Name@version`` install specs, sorted by name."""
        return tuple(entry.spec for entry in self.entries)

    @property
    def ok(self) -> bool:
        return not self.unknown


PINNED_LIBRARIES: dict[str, LibraryEntry] = {
    entry.arduino_name: entry
    for entry in (
        LibraryEntry(
            arduino_name="Adafruit BME280 Library",
            pinned_version="2.3.0",
            source_url=(
                "https://downloads.arduino.cc/libraries/github.com/adafruit/"
                "Adafruit_BME280_Library-2.3.0.zip"
            ),
            checksum="SHA-256:5aa74162cd944abb85143d2e9daa18b6dbe64f167f54c95d4e9e4f283573cfe5",
            repository="https://github.com/adafruit/Adafruit_BME280_Library.git",
            transitive_deps=("Adafruit Unified Sensor", "Adafruit BusIO"),
            notes="Used by bme280",
        ),
        LibraryEntry(
            arduino_name="Adafruit BusIO",
            pinned_version="1.17.4",
            source_url=(
                "https://downloads.arduino.cc/libraries/github.com/adafruit/"
                "Adafruit_BusIO-1.17.4.zip"
            ),
            checksum="SHA-256:c7bab1b6c6eee64b50964c8e149b795719a01f1d9dfcdcef170b35daa696349b",
            repository="https://github.com/adafruit/Adafruit_BusIO.git",
            notes="Transitive dependency of the Adafruit sensor and display libraries",
        ),
        LibraryEntry(
            arduino_name="Adafruit GFX Library",
            pinned_version="1.13.0",
            source_url=(
                "https://downloads.arduino.cc/libraries/github.com/adafruit/"
                "Adafruit_GFX_Library-1.13.0.zip"
            ),
            checksum="SHA-256:202ec716ff222fb08ffc544a5f315cb47b8630c35fbbc9944fc9f8c93d903924",
            repository="https://github.com/adafruit/Adafruit-GFX-Library.git",
            transitive_deps=("Adafruit BusIO",),
            notes="Used by ssd1306_oled",
        ),
        LibraryEntry(
            arduino_name="Adafruit NeoPixel",
            pinned_version="1.15.5",
            source_url=(
                "https://downloads.arduino.cc/libraries/github.com/adafruit/"
                "Adafruit_NeoPixel-1.15.5.zip"
            ),
            checksum="SHA-256:3d767caa011ab3078df7fe3c3e0456359ce2bc309eba343932690a2c1612fd16",
            repository="https://github.com/adafruit/Adafruit_NeoPixel.git",
            notes="Used by ws2812_strip",
        ),
        LibraryEntry(
            arduino_name="Adafruit SSD1306",
            pinned_version="2.5.17",
            source_url=(
                "https://downloads.arduino.cc/libraries/github.com/adafruit/"
                "Adafruit_SSD1306-2.5.17.zip"
            ),
            checksum="SHA-256:9b8a9c29a16576d66bce16ed9f6a731632fecbb477171f38a6bc033fda15dd06",
            repository="https://github.com/adafruit/Adafruit_SSD1306.git",
            transitive_deps=("Adafruit GFX Library",),
            notes="Used by ssd1306_oled",
        ),
        LibraryEntry(
            arduino_name="Adafruit Unified Sensor",
            pinned_version="1.1.15",
            source_url=(
                "https://downloads.arduino.cc/libraries/github.com/adafruit/"
                "Adafruit_Unified_Sensor-1.1.15.zip"
            ),
            checksum="SHA-256:d64cdec0b817535f568fc3b8b8d44e7e097749f5ab8040aea573f412a3cbaec6",
            repository="https://github.com/adafruit/Adafruit_Sensor.git",
            notes="Transitive dependency of dht22 and bme280",
        ),
        LibraryEntry(
            arduino_name="BH1750",
            pinned_version="1.3.0",
            source_url=("https://downloads.arduino.cc/libraries/github.com/claws/BH1750-1.3.0.zip"),
            checksum="SHA-256:740c39179835c056afd84eb3d90e23633d98e54252ad7de988c27d33f385bde2",
            repository="https://github.com/claws/BH1750.git",
            notes="Used by bh1750",
        ),
        LibraryEntry(
            arduino_name="DHT sensor library",
            pinned_version="1.4.7",
            source_url=(
                "https://downloads.arduino.cc/libraries/github.com/adafruit/"
                "DHT_sensor_library-1.4.7.zip"
            ),
            checksum="SHA-256:5c5d3881b49840c7be98cc0848a48930bbac364fadc7edb7965fe98f9bb1463f",
            repository="https://github.com/adafruit/DHT-sensor-library.git",
            transitive_deps=("Adafruit Unified Sensor",),
            notes="Used by dht22",
        ),
        LibraryEntry(
            arduino_name="ESP32Servo",
            pinned_version="3.2.1",
            source_url=(
                "https://downloads.arduino.cc/libraries/github.com/madhephaestus/"
                "ESP32Servo-3.2.1.zip"
            ),
            checksum="SHA-256:2fdebd81b1fb17cc52737ed1412ef23cd475ddec44e3a298f12d88cc881d9fa6",
            repository="https://github.com/madhephaestus/ESP32Servo.git",
            notes="Used by sg90_servo",
        ),
        LibraryEntry(
            arduino_name="RF24",
            pinned_version="1.6.2",
            source_url=("https://downloads.arduino.cc/libraries/github.com/TMRh20/RF24-1.6.2.zip"),
            checksum="SHA-256:22270a8f60e0dd90ad0a4fb3f0923e9054cba5e71c4da8afd65041fe00e2cf3e",
            repository="https://github.com/TMRh20/RF24.git",
            notes="Used by nrf24l01",
        ),
    )
}


def resolve_libraries(names: list[str] | tuple[str, ...]) -> LibraryResolution:
    """Resolve catalog library names to pinned entries.

    The result contains every requested library plus the transitive closure
    of their dependencies, sorted by name so the output is deterministic.
    Names that are not pinned are reported in ``unknown`` instead of being
    resolved to a floating version.
    """
    requested = tuple(names)
    wanted: set[str] = set()
    unknown: list[str] = []
    for name in requested:
        entry = PINNED_LIBRARIES.get(name)
        if entry is None:
            if name not in unknown:
                unknown.append(name)
            continue
        wanted.add(name)
    # Transitive closure over the pinned dependency graph.
    pending = list(wanted)
    while pending:
        name = pending.pop()
        entry = PINNED_LIBRARIES.get(name)
        if entry is None:
            continue
        for dep in entry.transitive_deps:
            if dep not in wanted:
                if dep not in PINNED_LIBRARIES:
                    if dep not in unknown:
                        unknown.append(dep)
                    continue
                wanted.add(dep)
                pending.append(dep)
    entries = tuple(PINNED_LIBRARIES[name] for name in sorted(wanted))
    return LibraryResolution(
        entries=entries,
        unknown=tuple(unknown),
        requested=requested,
    )


def pinned_spec(name: str) -> str:
    """The pinned ``Name@version`` install spec of one library name."""
    entry = PINNED_LIBRARIES.get(name)
    if entry is None:
        raise KeyError(f"library {name!r} is not pinned in the manifest")
    return entry.spec


def libraries_txt(names: list[str] | tuple[str, ...]) -> str:
    """The ``libraries.txt`` of a sketch: one pinned library per line."""
    resolution = resolve_libraries(names)
    lines = [entry.spec for entry in resolution.entries]
    return "\n".join(lines) + ("\n" if lines else "")


def write_manifest(out_dir: Path, names: list[str] | tuple[str, ...] = ()) -> tuple[Path, Path]:
    """Write ``libraries.txt`` and ``library_manifest.json`` into a folder.

    ``names`` are the library names the sketch uses.  When it is empty the
    whole pinned registry is written.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    resolution = (
        resolve_libraries(names)
        if names
        else LibraryResolution(
            entries=tuple(PINNED_LIBRARIES[name] for name in sorted(PINNED_LIBRARIES)),
            requested=tuple(sorted(PINNED_LIBRARIES)),
        )
    )
    txt = out_dir / "libraries.txt"
    txt.write_text(
        "".join(f"{entry.spec}\n" for entry in resolution.entries),
        encoding="utf-8",
    )
    js = out_dir / "library_manifest.json"
    data = {
        "index": LIBRARY_INDEX_URL,
        "libraries": [asdict(entry) for entry in resolution.entries],
    }
    js.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return txt, js


def _main(argv: list[str] | None = None) -> int:
    """Print the pinned library set, or write the manifest files.

    ``python -m sparkplan.firmware.manifest --install-specs`` prints one
    ``Name@version`` per line, which is what CI feeds to
    ``arduino-cli lib install``.
    """
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install-specs",
        action="store_true",
        help="print one pinned Name@version per line",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="write libraries.txt and library_manifest.json into this folder",
    )
    args = parser.parse_args(argv)

    if args.install_specs:
        for name in sorted(PINNED_LIBRARIES):
            print(PINNED_LIBRARIES[name].spec)
        return 0
    if args.out is not None:
        txt, js = write_manifest(args.out)
        print(f"wrote {txt}")
        print(f"wrote {js}")
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover - command line entry point
    raise SystemExit(_main())
