"""Phase 1 BuildSpec models.

A BuildSpec is the ONLY structured artifact the LLM is allowed to produce. It
describes a build in terms of catalog part ids and named pins/behaviours; it
never contains geometry or firmware code.

Two outcomes are modelled as a discriminated union on ``status``:

* ``status == "ok"``          -> :class:`SupportedBuild`
* ``status == "unsupported"`` -> :class:`UnsupportedBuild`

Units: millimetres, volts, milliamps.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Supported build
# ---------------------------------------------------------------------------


class PartRef(_Strict):
    """A single instantiation of a catalog part."""

    instance_id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    part_id: str = Field(min_length=1)
    quantity: int = Field(default=1, ge=1)
    label: str = ""


class Connection(_Strict):
    """A named electrical connection between two part pins."""

    from_instance: str = Field(min_length=1)
    from_pin: str = Field(min_length=1)
    to_instance: str = Field(min_length=1)
    to_pin: str = Field(min_length=1)
    note: str = ""


class LayoutHint(_Strict):
    part_id: str = Field(min_length=1)
    mount: Literal["base", "lid", "wall"]
    note: str = ""


class Behavior(_Strict):
    """A thing the build does, expressed in catalog/parts vocabulary."""

    name: str = Field(min_length=1)
    part_ids: list[str] = Field(default_factory=list)
    description: str = ""


class SupportedBuild(_Strict):
    status: Literal["ok"] = "ok"
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    parts: list[PartRef] = Field(min_length=1)
    connections: list[Connection] = Field(default_factory=list)
    layout: list[LayoutHint] = Field(default_factory=list)
    behaviors: list[Behavior] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_instances(self) -> SupportedBuild:
        ids = [p.instance_id for p in self.parts]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate instance_id in parts: {dupes}")
        if not any(p.part_id.startswith("esp32") for p in self.parts):
            raise ValueError("a supported build must include the ESP32 board part")
        return self

    def instance(self, instance_id: str) -> PartRef | None:
        for p in self.parts:
            if p.instance_id == instance_id:
                return p
        return None

    def part_on(self, instance_id: str) -> str:
        p = self.instance(instance_id)
        if p is None:
            raise KeyError(instance_id)
        return p.part_id


# ---------------------------------------------------------------------------
# Unsupported build
# ---------------------------------------------------------------------------


class UnsupportedBuild(_Strict):
    status: Literal["unsupported"] = "unsupported"
    title: str = Field(min_length=1, max_length=200)
    reasons: list[str] = Field(min_length=1)
    missing_capabilities: list[str] = Field(default_factory=list)
    closest_supported_alternative: str = ""


BuildSpec = Annotated[SupportedBuild | UnsupportedBuild, Field(discriminator="status")]

_BUILD_SPEC_ADAPTER: TypeAdapter[SupportedBuild | UnsupportedBuild] = TypeAdapter(BuildSpec)


def parse_build_spec(data: object) -> SupportedBuild | UnsupportedBuild:
    """Parse a raw dict/JSON value into the correct BuildSpec variant."""
    return _BUILD_SPEC_ADAPTER.validate_python(data)


def build_spec_json_schema() -> dict[str, object]:
    """JSON schema handed to the LLM for structured output."""
    return _BUILD_SPEC_ADAPTER.json_schema()
