"""Pin-level checks: GPIO validity, capability match, conflicts, level shifting."""

from __future__ import annotations

from sparkplan.catalog import Catalog
from sparkplan.models.spec import BuildSpec, Issue

# Peripheral pin kinds that expect a peripheral->controller INPUT path.
_INPUT_KINDS = {"digital_in"}
# Peripheral pin kinds that expect a controller OUTPUT path.
_OUTPUT_KINDS = {"digital_out", "pwm"}
_ANALOG_KINDS = {"analog_in"}
_I2C_KINDS = {"i2c_sda", "i2c_scl"}
_SPI_KINDS = {"spi_cs", "spi_sck", "spi_mosi", "spi_miso"}
_NEVER_CONNECT_KINDS = {"power_in", "power_out", "ground"}

_MULTI_GPIO_REQUIRED = {
    "hc_sr04": ["TRIG", "ECHO"],
    "nrf24l01": ["CE", "CSN"],
    "rgb_led_module": ["R", "G", "B"],
    "ky040_rotary_encoder": ["CLK", "DT", "SW"],
}


def _err(code: str, message: str, part_ids: list[str] | None = None, hint: str = "") -> Issue:
    return Issue(
        code=code, severity="error", message=message, part_ids=part_ids or [], fix_hint=hint
    )


def _warn(code: str, message: str, part_ids: list[str] | None = None, hint: str = "") -> Issue:
    return Issue(
        code=code, severity="warning", message=message, part_ids=part_ids or [], fix_hint=hint
    )


def _gpio_number(controller_pin: str) -> int:
    return int(controller_pin.removeprefix("GPIO"))


def validate_connections(spec: BuildSpec, catalog: Catalog) -> list[Issue]:
    issues: list[Issue] = []
    controller = catalog.controller
    gpio_table = catalog.gpios
    claimed: dict[str, list[str]] = {}

    for conn in spec.connections:
        inst = spec.instance(conn.instance_id)
        if inst is None:
            issues.append(
                _err(
                    "CONNECTION_UNKNOWN_INSTANCE",
                    f"connection references unknown instance {conn.instance_id!r}.",
                    hint="Every connection.instance_id must be an entry in parts.",
                )
            )
            continue
        part = catalog.part_or_none(inst.part_id)
        if part is None:
            continue  # UNKNOWN_PART already reported by structure validator

        pin = part.pin(conn.peripheral_pin)
        if pin is None:
            issues.append(
                _err(
                    "CONNECTION_UNKNOWN_PIN",
                    f"part {part.part_id} has no pin {conn.peripheral_pin!r}.",
                    part_ids=[part.part_id],
                    hint=f"Valid pins: {[p.name for p in part.pins]}.",
                )
            )
            continue

        if pin.kind in _NEVER_CONNECT_KINDS:
            issues.append(
                _err(
                    "CONNECTION_POWER_PIN",
                    f"pin {pin.name!r} of {inst.instance_id} is a power/ground pin; "
                    "do not list it as a signal connection.",
                    part_ids=[part.part_id],
                    hint="Power and ground are assigned automatically from the part's rail.",
                )
            )
            continue

        if controller.pin(conn.controller_pin) is None:
            issues.append(
                _err(
                    "CONTROLLER_PIN_MISSING",
                    f"{conn.controller_pin} is not broken out on the {controller.part_id}.",
                    hint="Use only GPIOs present in the catalog pinout.",
                )
            )
            continue

        gpio = _gpio_number(conn.controller_pin)
        claimed.setdefault(conn.controller_pin, []).append(f"{inst.instance_id}.{pin.name}")

        issues.extend(
            _check_gpio_usable(
                gpio, pin.kind, part.part_id, inst.instance_id, gpio_table, pin.level_shift_required
            )
        )
        issues.extend(
            _check_pin_kind_fit(pin.kind, gpio, part.part_id, inst.instance_id, gpio_table)
        )

    for controller_pin, users in claimed.items():
        if len(users) > 1:
            issues.append(
                _err(
                    "GPIO_CONFLICT",
                    f"{controller_pin} is claimed by multiple pins: {users}.",
                    hint="Assign each peripheral signal its own GPIO.",
                )
            )

    issues.extend(_check_multi_gpio_parts(spec, catalog, claimed))
    return issues


def _check_gpio_usable(
    gpio: int,
    pin_kind: str,
    part_id: str,
    instance_id: str,
    gpio_table,
    level_shift_required: bool,
) -> list[Issue]:
    issues: list[Issue] = []
    try:
        info = gpio_table.info(gpio)
    except Exception:  # noqa: BLE001 - unknown GPIO already reported
        return issues

    if info.reserved:
        issues.append(
            _err(
                "GPIO_RESERVED",
                f"GPIO{gpio} is reserved ({info.reserved}); it is not available for peripherals.",
                part_ids=[part_id],
                hint="Pick a free GPIO from the catalog GPIO table.",
            )
        )
        return issues

    if pin_kind in _OUTPUT_KINDS and "output" not in info.caps:
        issues.append(
            _err(
                "GPIO_INPUT_ONLY",
                f"GPIO{gpio} is input-only but {instance_id} drives it ({pin_kind}).",
                part_ids=[part_id],
                hint="Use a GPIO with output capability (e.g. GPIO4, 13, 14, 25-27, 32, 33).",
            )
        )

    if info.strap:
        issues.append(
            _warn(
                "GPIO_STRAP",
                f"GPIO{gpio} is a boot strap pin ({info.strap}); {instance_id} may affect boot.",
                part_ids=[part_id],
                hint="Prefer non-strap GPIOs unless you have no choice.",
            )
        )

    if pin_kind in _ANALOG_KINDS and not gpio_table.can_analog(gpio):
        issues.append(
            _err(
                "GPIO_NOT_ADC",
                f"GPIO{gpio} has no ADC channel but {instance_id} needs an analog input.",
                part_ids=[part_id],
                hint="Use an ADC1 GPIO (32, 33, 34, 35, 36, 39) for analog sensors.",
            )
        )

    if pin_kind in _ANALOG_KINDS and gpio_table.has_cap(gpio, "adc2"):
        issues.append(
            _warn(
                "ADC2_WIFI_CONFLICT",
                f"GPIO{gpio} is ADC2; readings are unreliable while Wi-Fi is active.",
                part_ids=[part_id],
                hint="Prefer ADC1 pins (GPIO32/33/34/35/36/39) for analog sensors.",
            )
        )

    if level_shift_required and pin_kind in _INPUT_KINDS:
        issues.append(
            _warn(
                "LEVEL_SHIFT_REQUIRED",
                f"{instance_id} outputs 5 V logic on {pin_kind}; ESP32 pins are not 5 V tolerant.",
                part_ids=[part_id],
                hint="Insert a voltage divider (e.g. 10k/20k) or level shifter on this line.",
            )
        )
    return issues


def _check_pin_kind_fit(
    pin_kind: str, gpio: int, part_id: str, instance_id: str, gpio_table
) -> list[Issue]:
    issues: list[Issue] = []
    if pin_kind in _I2C_KINDS:
        default = "i2c_sda" if pin_kind == "i2c_sda" else "i2c_scl"
        if gpio_table.defaults.get(default) != gpio:
            issues.append(
                _warn(
                    "I2C_NONDEFAULT_PIN",
                    f"{instance_id} uses GPIO{gpio} for {pin_kind}; default is "
                    f"GPIO{gpio_table.defaults.get(default)}.",
                    part_ids=[part_id],
                    hint=(
                        "Non-default I2C pins work on ESP32 (pin matrix) "
                        "but keep the bus consistent."
                    ),
                )
            )
    if pin_kind in _SPI_KINDS and pin_kind != "spi_cs":
        default = gpio_table.defaults.get(pin_kind)
        if default is not None and default != gpio:
            issues.append(
                _warn(
                    "SPI_NONDEFAULT_PIN",
                    f"{instance_id} uses GPIO{gpio} for {pin_kind}; VSPI default is "
                    f"GPIO{default}.",
                    part_ids=[part_id],
                    hint="Keep SPI on the default VSPI pins where possible.",
                )
            )
    return issues


def _check_multi_gpio_parts(
    spec: BuildSpec, catalog: Catalog, claimed: dict[str, list[str]]
) -> list[Issue]:
    issues: list[Issue] = []
    for inst in spec.parts:
        required = _MULTI_GPIO_REQUIRED.get(inst.part_id)
        if not required:
            continue
        part = catalog.part(inst.part_id)
        wired = {
            c.peripheral_pin
            for c in spec.connections
            if c.instance_id == inst.instance_id and part.pin(c.peripheral_pin) is not None
        }
        missing = [p for p in required if p not in wired]
        if missing:
            issues.append(
                _err(
                    "MULTI_GPIO_INCOMPLETE",
                    f"{inst.instance_id} ({part.part_id}) is missing connections for "
                    f"pins {missing}.",
                    part_ids=[part.part_id],
                    hint=(
                        f"Connect every signal pin; this part needs at least "
                        f"{len(required)} GPIOs."
                    ),
                )
            )
    return issues
