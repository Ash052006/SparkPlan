"""Catalog data models and loader.

The catalog is the single source of truth for what SparkPlan may recommend.
Everything on disk is validated with Pydantic at load time: unknown fields,
duplicate ids, or parts with no pins fail loudly with ``CatalogError``.

Units everywhere: millimetres, volts, milliamps.
"""

from __future__ import annotations

import json
import os
import re
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CatalogError(RuntimeError):
    """Raised when the on-disk catalog is missing, malformed, or inconsistent."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Category(str, Enum):
    BOARD = "board"
    SENSOR = "sensor"
    DISPLAY = "display"
    ACTUATOR = "actuator"
    INPUT = "input"
    PASSIVE = "passive"


class Interface(str, Enum):
    DIGITAL = "digital"
    ANALOG = "analog"
    I2C = "i2c"
    PWM = "pwm"
    UART = "uart"
    NONE = "none"


class PinRole(str, Enum):
    VCC = "vcc"
    GND = "gnd"
    SIGNAL = "signal"
    SDA = "sda"
    SCL = "scl"
    TRIG = "trig"
    ECHO = "echo"


class PinDirection(str, Enum):
    IN = "in"
    OUT = "out"
    BIDIR = "bidir"
    POWER = "power"


class Mount(str, Enum):
    BASE = "base"
    LID = "lid"
    WALL = "wall"


class ExposureFace(str, Enum):
    NONE = "none"
    TOP = "top"
    BOTTOM = "bottom"
    LEFT = "left"
    RIGHT = "right"
    FRONT = "front"
    BACK = "back"


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SupplyVoltage(_Strict):
    min_v: float
    max_v: float

    @model_validator(mode="after")
    def _ordered(self) -> SupplyVoltage:
        if self.min_v > self.max_v:
            raise ValueError(f"supply min {self.min_v} V exceeds max {self.max_v} V")
        return self


class Pin(_Strict):
    name: str = Field(min_length=1)
    role: PinRole
    direction: PinDirection


class DimensionsMm(_Strict):
    x: float = Field(gt=0)
    y: float = Field(gt=0)
    z: float = Field(gt=0)


class MountingHole(_Strict):
    x: float
    y: float
    diameter: float = Field(gt=0)


class Exposure(_Strict):
    """Which face needs an opening, and how big."""

    face: ExposureFace = ExposureFace.NONE
    opening_x_mm: float = Field(default=0.0, ge=0)
    opening_y_mm: float = Field(default=0.0, ge=0)
    offset_x_mm: float = 0.0
    offset_y_mm: float = 0.0

    @model_validator(mode="after")
    def _opening_required_for_real_face(self) -> Exposure:
        if self.face != ExposureFace.NONE and (self.opening_x_mm <= 0 or self.opening_y_mm <= 0):
            raise ValueError(
                f"exposure face {self.face.value!r} needs positive opening_x_mm/opening_y_mm"
            )
        return self


# ---------------------------------------------------------------------------
# Firmware template
# ---------------------------------------------------------------------------


class FirmwareReading(_Strict):
    """One value the part can produce.

    ``type`` is the C++ type of the generated variable (``float``, ``int`` or
    ``bool``).  It is optional: when empty the generator falls back to the
    documented unit table (``bool``/``boolean`` -> bool, ``adc_counts`` -> int,
    anything else -> float).

    ``expression`` is the C++ expression that yields the value, evaluated in
    the scope of the part's ``read_snippet`` (so it usually names a local
    declared there).  It is what binds a catalog reading to a snippet local
    without guessing.
    """

    name: str = Field(min_length=1)
    unit: str = ""
    type: str = ""
    expression: str = ""

    @field_validator("type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v and v not in {"float", "int", "bool"}:
            raise ValueError(f"reading type must be float, int or bool, got {v!r}")
        return v


class FirmwareAction(_Strict):
    name: str = Field(min_length=1)
    snippet: str = ""
    params: list[str] = Field(default_factory=list)


class Firmware(_Strict):
    """Per-part firmware template.

    ``object_name`` is the C++ object the template declares (``dht`` for the
    DHT22).  The generator scopes it per instance so two instances of the
    same part do not collide.

    ``pin_map`` maps a template pin placeholder (``LED_PIN``) to a catalog pin
    name (``ANODE``).  A placeholder that already matches the convention
    ``<PIN NAME>_PIN`` needs no entry.

    ``min_interval_ms`` is the shortest interval at which the part may be
    sampled, in milliseconds.  It is a property of the part protocol, not a
    user choice: the DHT sensor library refuses a second read within 2000 ms
    and answers with the previous measurement, and the HC-SR04 needs 60 ms
    between triggers so its echoes do not overlap.  Zero means "no constraint
    beyond the sketch tick".
    """

    library_deps: list[str] = Field(default_factory=list)
    includes: list[str] = Field(default_factory=list)
    globals: list[str] = Field(default_factory=list)
    setup_lines: list[str] = Field(default_factory=list)
    readings: list[FirmwareReading] = Field(default_factory=list)
    read_snippet: str = ""
    actions: list[FirmwareAction] = Field(default_factory=list)
    object_name: str = ""
    pin_map: dict[str, str] = Field(default_factory=dict)
    min_interval_ms: int = Field(default=0, ge=0)

    @field_validator("object_name")
    @classmethod
    def _identifier(cls, v: str) -> str:
        if v and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v):
            raise ValueError(f"object_name must be a C++ identifier, got {v!r}")
        return v

    @field_validator("pin_map")
    @classmethod
    def _placeholders(cls, v: dict[str, str]) -> dict[str, str]:
        for placeholder, pin_name in v.items():
            if not re.fullmatch(r"[A-Z][A-Z0-9_]*_PIN", placeholder):
                raise ValueError(
                    f"pin_map key {placeholder!r} must be an upper-case placeholder "
                    "such as 'AOUT_PIN'"
                )
            if not pin_name:
                raise ValueError(f"pin_map[{placeholder!r}] must name a catalog pin")
        return v


# ---------------------------------------------------------------------------
# Board-specific models
# ---------------------------------------------------------------------------


class Rail(_Strict):
    name: str = Field(min_length=1)
    voltage_v: float
    max_current_ma: float = Field(gt=0)
    source_note: str
    approximate: bool = True
    verify_against_datasheet: bool = True


class GpioCapabilities(_Strict):
    adc: bool = False
    pwm: bool = False
    touch: bool = False
    i2c: bool = False
    spi: bool = False
    output: bool = True


class GpioPin(_Strict):
    gpio: int = Field(ge=0, le=39)
    caps: GpioCapabilities
    input_only: bool = False
    forbidden: bool = False
    strapping: bool = False
    default_i2c_sda: bool = False
    default_i2c_scl: bool = False
    source_note: str
    verify_against_datasheet: bool = True

    @model_validator(mode="after")
    def _consistency(self) -> GpioPin:
        if self.input_only and self.caps.output:
            raise ValueError(f"GPIO{self.gpio}: input_only pin cannot have output capability")
        if self.forbidden and (self.caps.adc or self.caps.pwm or self.caps.i2c):
            raise ValueError(f"GPIO{self.gpio}: forbidden (flash) pin cannot carry peripheral caps")
        return self


class BoardModel(_Strict):
    rails: list[Rail]
    gpio: list[GpioPin]
    default_i2c_sda_gpio: int = Field(ge=0, le=39)
    default_i2c_scl_gpio: int = Field(ge=0, le=39)
    source_note: str
    verify_against_datasheet: bool = True

    @model_validator(mode="after")
    def _unique_gpio(self) -> BoardModel:
        seen = [g.gpio for g in self.gpio]
        if len(seen) != len(set(seen)):
            raise ValueError("duplicate gpio number in board model")
        return self

    def pin(self, gpio: int) -> GpioPin | None:
        for g in self.gpio:
            if g.gpio == gpio:
                return g
        return None

    def usable_output(self) -> list[int]:
        return [g.gpio for g in self.gpio if g.caps.output and not g.input_only and not g.forbidden]

    def adc_pins(self) -> list[int]:
        return [g.gpio for g in self.gpio if g.caps.adc]

    def pwm_pins(self) -> list[int]:
        return [g.gpio for g in self.gpio if g.caps.pwm]


# ---------------------------------------------------------------------------
# Part
# ---------------------------------------------------------------------------


class Part(_Strict):
    id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1)
    category: Category
    description: str = ""
    supply_voltage_v: SupplyVoltage
    logic_level_v: float = Field(ge=0)
    typical_current_ma: float = Field(ge=0)
    peak_current_ma: float = Field(ge=0)
    interface: Interface
    i2c_address: str | None = None
    pins: list[Pin] = Field(min_length=1)
    dimensions_mm: DimensionsMm
    dimensions_verified: bool = False
    dimensions_source: str | None = None
    dimensions_basis: Literal["manufacturer_spec", "measured", "estimate", "unknown"] = "unknown"

    @field_validator("dimensions_source")
    @classmethod
    def _source_is_text(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError("dimensions_source must be a string or null")
        return v

    @model_validator(mode="after")
    def _provenance_consistency(self) -> Part:
        needs_source = self.dimensions_basis in {"manufacturer_spec", "measured"}
        has_source = bool((self.dimensions_source or "").strip())
        if needs_source and not has_source:
            raise ValueError(
                f"part {self.id!r} requires a non-empty dimensions_source "
                f"for basis {self.dimensions_basis!r}"
            )
        if self.dimensions_basis == "estimate" and self.dimensions_verified:
            raise ValueError(f"part {self.id!r} cannot be verified while its basis is an estimate")
        return self

    mounting_holes: list[MountingHole] = Field(default_factory=list)
    mount: Mount
    exposure: Exposure = Field(default_factory=Exposure)
    heat_source: bool = False
    capabilities: list[str] = Field(default_factory=list)
    firmware: Firmware = Field(default_factory=Firmware)
    notes: str = ""
    board: BoardModel | None = None

    @field_validator("i2c_address")
    @classmethod
    def _address_format(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not isinstance(v, str) or not v.startswith("0x") or len(v) != 4:
            raise ValueError(f"i2c_address {v!r} must look like '0x3C'")
        int(v, 16)  # raises if not hex
        return v

    @model_validator(mode="after")
    def _consistency(self) -> Part:
        pin_names = [p.name for p in self.pins]
        if len(pin_names) != len(set(pin_names)):
            raise ValueError(f"part {self.id!r} has duplicate pin names")

        if self.interface is Interface.I2C and self.i2c_address is None:
            raise ValueError(f"part {self.id!r} uses i2c but has no i2c_address")
        if self.interface is not Interface.I2C and self.i2c_address is not None:
            raise ValueError(
                f"part {self.id!r} has i2c_address but interface is {self.interface.value}"
            )

        if self.category is Category.BOARD and self.board is None:
            raise ValueError(f"board part {self.id!r} must include a board model")
        if self.category is not Category.BOARD and self.board is not None:
            raise ValueError(f"non-board part {self.id!r} must not include a board model")

        if self.peak_current_ma and self.peak_current_ma < self.typical_current_ma:
            raise ValueError(f"part {self.id!r} peak current below typical current")

        for placeholder, pin_name in self.firmware.pin_map.items():
            if self.pin(pin_name) is None:
                raise ValueError(
                    f"part {self.id!r} pin_map[{placeholder!r}] names unknown pin {pin_name!r}"
                )
        return self

    def pin(self, name: str) -> Pin | None:
        for p in self.pins:
            if p.name == name:
                return p
        return None

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class Catalog(BaseModel):
    """A validated collection of parts. Nothing outside it may be recommended."""

    model_config = ConfigDict(extra="forbid")

    parts: list[Part]

    @model_validator(mode="after")
    def _unique_ids(self) -> Catalog:
        ids = [p.id for p in self.parts]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate part ids: {dupes}")
        return self

    def get(self, part_id: str) -> Part:
        for p in self.parts:
            if p.id == part_id:
                return p
        raise CatalogError(f"unknown part id {part_id!r}")

    def get_or_none(self, part_id: str) -> Part | None:
        for p in self.parts:
            if p.id == part_id:
                return p
        return None

    def by_capability(self, capability: str) -> list[Part]:
        return [p for p in self.parts if p.has_capability(capability)]

    def board(self) -> Part:
        for p in self.parts:
            if p.category is Category.BOARD:
                return p
        raise CatalogError("catalog has no board part")

    def all_capabilities(self) -> list[str]:
        caps: set[str] = set()
        for p in self.parts:
            caps.update(p.capabilities)
        return sorted(caps)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def default_catalog_path() -> Path:
    env = os.environ.get("SPARKPLAN_CATALOG")
    if env:
        return Path(env) / "parts.json"
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "catalog" / "parts.json"
        if candidate.is_file():
            return candidate
    raise CatalogError("could not locate catalog/parts.json (set SPARKPLAN_CATALOG)")


def load_catalog(path: str | Path | None = None) -> Catalog:
    """Load and fully validate the catalog. Fails loudly on any problem."""
    catalog_path = Path(path) if path is not None else default_catalog_path()
    try:
        raw = json.loads(catalog_path.read_text())
    except FileNotFoundError as exc:
        raise CatalogError(f"catalog file not found: {catalog_path}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"invalid JSON in {catalog_path}: {exc}") from exc

    if isinstance(raw, dict) and "parts" in raw:
        payload = raw
    elif isinstance(raw, list):
        payload = {"parts": raw}
    else:
        raise CatalogError(f"{catalog_path}: expected a list of parts or an object with 'parts'")

    try:
        return Catalog.model_validate(payload)
    except Exception as exc:  # pydantic ValidationError and friends
        raise CatalogError(f"invalid catalog {catalog_path}: {exc}") from exc


@lru_cache(maxsize=4)
def _cached_catalog(path_str: str | None) -> Catalog:
    return load_catalog(path_str)


def get_catalog(path: str | Path | None = None) -> Catalog:
    """Cached loader for the (single) catalog."""
    return _cached_catalog(str(path) if path is not None else None)


def reset_catalog_cache() -> None:
    _cached_catalog.cache_clear()


# Import-time validation: a broken catalog must not import cleanly.
CATALOG: Catalog = get_catalog()
