"""Loading and querying the parts catalog.

The catalog is the single source of truth for what may be recommended.
Everything on disk is validated with Pydantic at load time; a corrupt
catalog fails loudly at startup, not at plan time.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from sparkplan.models.spec import (
    FirmwareApiFn,
    Part,
    PartInstance,
)


class CatalogError(RuntimeError):
    """Raised when the on-disk catalog is missing or invalid."""


class GpioInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caps: list[str] = Field(default_factory=list)
    strap: str | None = None
    reserved: str | None = None
    notes: str = ""


class Rail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    voltage: float
    max_ma: float
    source: str = ""
    controller_share_ma: float = 0.0


class PowerRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    boost_part_required_for_5v_over_budget: bool = True
    boost_output_ma_default: int = 1000
    battery_part_required_for_boost: bool = True


class PowerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    controller: str
    rails: dict[str, Rail]
    external_supply_rules: PowerRules = Field(default_factory=PowerRules)


class GpioTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    controller: str
    gpios: dict[str, GpioInfo]
    defaults: dict[str, int] = Field(default_factory=dict)

    def info(self, gpio: int) -> GpioInfo:
        key = str(gpio)
        if key not in self.gpios:
            raise CatalogError(f"GPIO {gpio} is not broken out on {self.controller}")
        return self.gpios[key]

    def has_cap(self, gpio: int, cap: str) -> bool:
        return cap in self.info(gpio).caps

    def can_output(self, gpio: int) -> bool:
        info = self.info(gpio)
        return "output" in info.caps and info.reserved is None

    def can_input(self, gpio: int) -> bool:
        caps = self.info(gpio).caps
        return "input" in caps or "input_only" in caps

    def can_analog(self, gpio: int) -> bool:
        return self.has_cap(gpio, "adc1") or self.has_cap(gpio, "adc2")

    def is_free(self, gpio: int) -> bool:
        info = self.info(gpio)
        return info.reserved is None


class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    keywords: list[str] = Field(default_factory=list)
    parts: list[str] = Field(default_factory=list)


class CapabilityIndex(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capabilities: list[Capability]
    refusal_keywords: list[str] = Field(default_factory=list)
    refusal_note: str = ""

    def match(self, idea: str) -> list[Capability]:
        """Keyword match of idea text against catalog capabilities."""
        text = idea.lower()
        hits = [cap for cap in self.capabilities if any(kw in text for kw in cap.keywords)]
        return hits

    def refusals_for(self, idea: str) -> list[str]:
        text = idea.lower()
        return [kw for kw in self.refusal_keywords if kw in text]


class Catalog:
    """In-memory catalog: parts + power model + GPIO table + capabilities."""

    def __init__(
        self,
        parts: dict[str, Part],
        power: PowerModel,
        gpios: GpioTable,
        capabilities: CapabilityIndex,
    ) -> None:
        self._parts = parts
        self.power = power
        self.gpios = gpios
        self.capabilities = capabilities

    @property
    def parts(self) -> dict[str, Part]:
        return dict(self._parts)

    @property
    def controller(self) -> Part:
        return self._parts[self.power.controller]

    def part(self, part_id: str) -> Part:
        if part_id not in self._parts:
            raise CatalogError(f"unknown part_id {part_id!r}")
        return self._parts[part_id]

    def part_or_none(self, part_id: str) -> Part | None:
        return self._parts.get(part_id)

    def api_fn(self, part_id: str, api_name: str) -> FirmwareApiFn | None:
        for fn in self.part(part_id).firmware.api:
            if fn.name == api_name:
                return fn
        return None

    def instance_part(self, inst: PartInstance) -> Part:
        return self.part(inst.part_id)

    def bom(self, spec_parts: list[PartInstance]) -> list[tuple[PartInstance, Part]]:
        return [(pi, self.part(pi.part_id)) for pi in spec_parts]


def default_catalog_path() -> Path:
    env = os.environ.get("SPARKPLAN_CATALOG")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "catalog"
        if (candidate / "parts").is_dir():
            return candidate
    raise CatalogError("could not locate catalog/ directory (set SPARKPLAN_CATALOG)")


def _load_json(path: Path) -> dict | list:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise CatalogError(f"missing catalog file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"invalid JSON in {path}: {exc}") from exc


@lru_cache(maxsize=4)
def load_catalog(path_str: str | None = None) -> Catalog:
    root = Path(path_str) if path_str else default_catalog_path()
    parts: dict[str, Part] = {}
    for entry in sorted((root / "parts").glob("*.json")):
        data = _load_json(entry)
        try:
            part = Part.model_validate(data)
        except Exception as exc:  # noqa: BLE001 - re-raised as CatalogError
            raise CatalogError(f"invalid part file {entry}: {exc}") from exc
        if part.part_id != entry.stem:
            raise CatalogError(f"{entry}: part_id {part.part_id!r} does not match filename")
        if part.part_id in parts:
            raise CatalogError(f"duplicate part_id {part.part_id!r}")
        parts[part.part_id] = part

    power = PowerModel.model_validate(_load_json(root / "power.json"))
    gpios = GpioTable.model_validate(_load_json(root / "esp32_gpio.json"))
    capabilities = CapabilityIndex.model_validate(_load_json(root / "capabilities.json"))

    missing = [c.parts for c in capabilities.capabilities for pid in c.parts if pid not in parts]
    if missing:
        raise CatalogError(f"capabilities.json references unknown parts: {missing}")

    return Catalog(parts=parts, power=power, gpios=gpios, capabilities=capabilities)


def reset_catalog_cache() -> None:
    load_catalog.cache_clear()
