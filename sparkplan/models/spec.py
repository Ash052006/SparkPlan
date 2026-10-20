"""Structured issues and core build-spec models.

Units everywhere: millimetres, volts, milliamps.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["error", "warning", "info"]


class Issue(BaseModel):
    """A deterministic validation finding.

    Every validator returns Issue objects; nothing raises for bad input.
    """

    model_config = ConfigDict(extra="forbid")

    code: Annotated[str, Field(description="Stable machine-readable code, e.g. UNKNOWN_PART.")]
    severity: Severity
    message: Annotated[str, Field(description="Human-readable explanation.")]
    part_ids: Annotated[list[str], Field(description="Parts involved.")] = Field(
        default_factory=list
    )
    fix_hint: Annotated[str, Field(default="", description="What the planner should change.")]


class Category(str, Enum):
    CONTROLLER = "controller"
    SENSOR = "sensor"
    ACTUATOR = "actuator"
    DISPLAY = "display"
    INPUT = "input"
    COMMS = "comms"
    POWER = "power"


class PinKind(str, Enum):
    POWER_IN = "power_in"
    POWER_OUT = "power_out"
    GROUND = "ground"
    DIGITAL_IN = "digital_in"
    DIGITAL_OUT = "digital_out"
    ANALOG_IN = "analog_in"
    PWM = "pwm"
    I2C_SDA = "i2c_sda"
    I2C_SCL = "i2c_scl"
    SPI_CS = "spi_cs"
    SPI_SCK = "spi_sck"
    SPI_MOSI = "spi_mosi"
    SPI_MISO = "spi_miso"
    BIDIR = "bidir"
    INPUT_ONLY = "input_only"
    UART0_TX = "uart0_tx"
    UART0_RX = "uart0_rx"


class CatalogPin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str
    logic_v: float = 3.3
    gpio: int | None = None
    active: str | None = None
    pull: str | None = None
    level_shift_required: bool = False


class MountingHole(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x_mm: float
    y_mm: float
    diameter_mm: float


class UsbConnector(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    edge: str
    offset_mm: float
    width_mm: float
    height_mm: float


class Mechanical(BaseModel):
    model_config = ConfigDict(extra="forbid")

    width_mm: float
    length_mm: float
    height_mm: float
    board_thickness_mm: float = 0.0
    mounting_holes: list[MountingHole] = Field(default_factory=list)
    usb_connector: UsbConnector | None = None


class Supply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rail: str
    voltage_min: float
    voltage_max: float
    i_ma_typ: float
    i_ma_max: float
    notes: str = ""


class PowerRole(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    output_rail: str | None = None
    output_ma: int | None = None
    requires_battery: bool = False
    voltage: float | None = None
    ma: int | None = None


class FirmwareApiFn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str
    returns: str
    unit: str = ""
    param: str | None = None
    fn: str


class FirmwareTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    libraries: list[str] = Field(default_factory=list)
    includes: list[str] = Field(default_factory=list)
    defines: list[str] = Field(default_factory=list)
    globals: list[str] = Field(default_factory=list)
    setup: list[str] = Field(default_factory=list)
    functions: list[str] = Field(default_factory=list)
    api: list[FirmwareApiFn] = Field(default_factory=list)
    requires_two_gpios: bool = False
    requires_three_gpios: bool = False
    bus: str | None = None
    i2c_address: str | None = None


class Part(BaseModel):
    """One catalog part. Nothing outside the catalog may be recommended."""

    model_config = ConfigDict(extra="forbid")

    part_id: str
    name: str
    category: Category
    description: str = ""
    supply: Supply
    pins: list[CatalogPin]
    mechanical: Mechanical
    power_role: PowerRole | None = None
    firmware: FirmwareTemplate

    def pin(self, name: str) -> CatalogPin | None:
        for p in self.pins:
            if p.name == name:
                return p
        return None

    def signal_pins(self) -> list[CatalogPin]:
        skip = {PinKind.POWER_IN.value, PinKind.POWER_OUT.value, PinKind.GROUND.value}
        return [p for p in self.pins if p.kind not in skip]


# ---------------------------------------------------------------------------
# BuildSpec - the ONLY thing the LLM is allowed to produce.
# ---------------------------------------------------------------------------


class PartInstance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,30}$")]
    part_id: str


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: str
    peripheral_pin: str
    controller_pin: Annotated[str, Field(pattern=r"^GPIO\d{1,2}$")]


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")]
    kind: Literal["read", "write"]
    instance_id: str
    api: Annotated[str, Field(description="Name of a firmware api function on the part.")]
    consumes: str | None = Field(
        default=None, description="action_id whose value this action consumes."
    )
    params: dict[str, Any] | None = None


class BuildSpec(BaseModel):
    """Deterministic, machine-checkable description of a build."""

    model_config = ConfigDict(extra="forbid")

    idea: Annotated[str, Field(min_length=1, max_length=2000)]
    controller: Literal["esp32_devkit_v4"] = "esp32_devkit_v4"
    parts: list[PartInstance]
    connections: list[Connection]
    actions: list[Action]
    notes: str = ""

    def instance(self, instance_id: str) -> PartInstance | None:
        for p in self.parts:
            if p.instance_id == instance_id:
                return p
        return None


class Refusal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasons: Annotated[list[str], Field(min_length=1)]
    detected_capabilities: list[str] = Field(default_factory=list)


class PlanStatus(str, Enum):
    OK = "ok"
    REFUSED = "refused"
    EXHAUSTED = "exhausted_repair_budget"


class PlanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: PlanStatus
    spec: BuildSpec | None = None
    refusal: Refusal | None = None
    issues: list[Issue] = Field(default_factory=list)
    repair_rounds: int = 0
