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

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


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
    """A named electrical connection between two part pins.

    ``signal_role`` describes the electrical meaning of the net and is what
    the validators key on:

    * ``signal``  - a GPIO carrying data/control (default)
    * ``rail``    - power into a part pin from a board rail (``rail_name`` set)
    * ``gnd``     - ground
    * ``sda`` / ``scl`` - shared I2C bus lines
    * ``passive`` - a series/pass-through element such as a resistor or divider
    """

    from_instance: str = Field(min_length=1)
    from_pin: str = Field(min_length=1)
    to_instance: str = Field(min_length=1)
    to_pin: str = Field(min_length=1)
    signal_role: Literal["signal", "rail", "gnd", "sda", "scl", "passive"] = "signal"
    rail_name: str | None = None
    note: str = ""


class LayoutHint(_Strict):
    part_id: str = Field(min_length=1)
    mount: Literal["base", "lid", "wall"]
    note: str = ""


class Reading(_Strict):
    """A value the firmware reads, exposed for use by rules/display lines."""

    name: str = Field(min_length=1)
    unit: str = ""
    instance_id: str = ""


class Rule(_Strict):
    """A conditional behaviour: when ``condition_reading`` compares true, the
    ``action_reading`` (or ``action_actuator``) is acted upon."""

    name: str = Field(min_length=1)
    condition_reading: str = Field(min_length=1)
    condition_unit: str = ""
    comparator: Literal[">", ">=", "<", "<=", "==", "!="] = ">"
    threshold: float = 0.0
    action_reading: str = ""
    action_actuator: str = ""


class DisplayLine(_Strict):
    """A line shown on a display, referencing an existing reading."""

    target_instance: str = Field(min_length=1)
    reading: str = Field(min_length=1)
    unit: str = ""
    text: str = ""


class Behavior(_Strict):
    """A thing the build does, expressed in catalog/parts vocabulary."""

    name: str = Field(min_length=1)
    part_ids: list[str] = Field(default_factory=list)
    description: str = ""
    readings: list[Reading] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)
    display_lines: list[DisplayLine] = Field(default_factory=list)


class SupportedBuild(_Strict):
    """A supported build.

    Structural problems (duplicate instance ids, missing/duplicated ESP32,
    unknown part ids) are NOT raised here: the V1 membership validator reports
    them as Issues so the LLM gets machine-readable feedback. The only
    structural guarantee enforced at parse time is that at least one part
    exists, because an empty build is not a build at all.
    """

    status: Literal["ok"] = "ok"
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    parts: list[PartRef] = Field(min_length=1)
    connections: list[Connection] = Field(default_factory=list)
    layout: list[LayoutHint] = Field(default_factory=list)
    behaviors: list[Behavior] = Field(default_factory=list)

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

    def board_instances(self) -> list[PartRef]:
        return [p for p in self.parts if p.part_id.startswith("esp32")]


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
