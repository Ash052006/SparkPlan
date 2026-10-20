"""V2 pins: referenced pins exist and fit the GPIO capability.

Rules:
* every referenced peripheral pin must exist on the catalog part (error)
* every referenced controller target must be a real board pin (error)
* the pin's role must fit the GPIO capability:
  - analog signal pin  -> GPIO must have ADC
  - PWM interface pin  -> GPIO must have PWM
  - digital output     -> GPIO must support output
  - digital input      -> GPIO must be usable as an input
* flash-reserved pins (6-11) are errors
* input-only pins (34/35/36/39) used as outputs are errors
* strapping pins (0/2/12/15) are warnings
"""

from __future__ import annotations

from sparkplan.models.buildspec import SupportedBuild
from sparkplan.models.catalog import BoardModel, Catalog, Interface, PinDirection
from sparkplan.models.spec import Issue
from sparkplan.validators.phase2.report import issue


def _controller_gpio(pin: str) -> int | None:
    """Parse a controller pin like 'D4', 'GPIO4', or '3V3'/'GND'."""
    cleaned = pin.strip().upper()
    if cleaned.startswith("GPIO"):
        cleaned = cleaned[4:]
    elif cleaned.startswith("D") and cleaned[1:].isdigit():
        cleaned = cleaned[1:]
    if cleaned.isdigit():
        return int(cleaned)
    return None


def validate_pins(spec: SupportedBuild, catalog: Catalog, board: BoardModel) -> list[Issue]:
    issues: list[Issue] = []
    known_board_pins = {g.gpio for g in board.gpio}

    for conn in spec.connections:
        part = (
            catalog.get_or_none(spec.part_on(conn.from_instance))
            if spec.instance(conn.from_instance)
            else None
        )

        # peripheral pin exists on the catalog part
        pin = part.pin(conn.from_pin) if part is not None else None
        if part is not None and pin is None:
            issues.append(
                issue(
                    "PIN_UNKNOWN",
                    f"{conn.from_instance} ({part.id}) has no pin named {conn.from_pin!r}.",
                    part_ids=[conn.from_instance],
                    fix_hint=f"Use one of the part's pins: {[p.name for p in part.pins]}.",
                )
            )

        # power/gnd/passive nets are not GPIO signals
        if conn.signal_role in {"rail", "gnd", "passive"}:
            continue

        # controller-side checks only apply when the connection targets the
        # board; a connection between two peripherals (e.g. sensor -> divider)
        # has no GPIO on the receiving end.
        to_part = (
            catalog.get_or_none(spec.part_on(conn.to_instance))
            if spec.instance(conn.to_instance)
            else None
        )
        if to_part is None or to_part.category.value != "board":
            continue

        # controller side must be a real board pin
        gpio = _controller_gpio(conn.to_pin)
        if gpio is None:
            issues.append(
                issue(
                    "CONTROLLER_PIN_MISSING",
                    f"{conn.to_instance} pin {conn.to_pin!r} is not a numbered GPIO.",
                    part_ids=[conn.to_instance],
                    fix_hint="Target a numbered GPIO such as 'D4' or 'GPIO21'.",
                )
            )
            continue
        if gpio not in known_board_pins:
            issues.append(
                issue(
                    "CONTROLLER_PIN_MISSING",
                    f"GPIO{gpio} does not exist on the board model.",
                    part_ids=[conn.to_instance],
                    fix_hint="Pick an existing board GPIO.",
                )
            )
            continue

        gpio_pin = board.pin(gpio)
        assert gpio_pin is not None
        involved = [conn.from_instance, conn.to_instance]

        # flash-reserved: hard error
        if gpio_pin.forbidden:
            issues.append(
                issue(
                    "GPIO_FLASH_RESERVED",
                    f"GPIO{gpio} is wired to the SPI flash and must never be used.",
                    part_ids=involved,
                    fix_hint="Move this signal to a general-purpose GPIO.",
                )
            )

        # strapping: warning
        if gpio_pin.strapping:
            issues.append(
                issue(
                    "GPIO_STRAPPING",
                    f"GPIO{gpio} is a boot strapping pin; it affects boot mode.",
                    severity="warning",
                    part_ids=involved,
                    fix_hint="Prefer a non-strapping GPIO unless intended.",
                )
            )

        if pin is None:
            continue

        # The role must fit the GPIO capability.
        # The board must *drive* only when the peripheral pin is an input, or
        # when a PWM interface needs a square wave from the GPIO. A peripheral
        # output/bidir pin feeds the GPIO, so an input-only GPIO is fine here.
        board_drives = pin.direction is PinDirection.IN or (
            part is not None and part.interface is Interface.PWM
        )

        if part is not None and part.interface is Interface.ANALOG and not gpio_pin.caps.adc:
            issues.append(
                issue(
                    "GPIO_NOT_ADC_HERE",
                    f"{part.id} pin {pin.name} is analog but GPIO{gpio} has no ADC.",
                    part_ids=involved,
                    fix_hint="Use an ADC-capable GPIO (e.g. 32, 33, 34, 35, 25-27).",
                )
            )
        if part is not None and part.interface is Interface.PWM and not gpio_pin.caps.pwm:
            issues.append(
                issue(
                    "GPIO_NOT_PWM",
                    f"{part.id} pin {pin.name} needs PWM but GPIO{gpio} has none.",
                    part_ids=involved,
                    fix_hint="Use a PWM-capable GPIO.",
                )
            )
        if board_drives and gpio_pin.input_only:
            issues.append(
                issue(
                    "GPIO_INPUT_ONLY",
                    f"GPIO{gpio} is input-only but {part.id if part else conn.from_instance} "
                    f"pin {pin.name} must be driven by the board.",
                    part_ids=involved,
                    fix_hint="Use a GPIO that can drive outputs (not 34/35/36/39).",
                )
            )
        elif board_drives and not gpio_pin.caps.output:
            who = part.id if part else conn.from_instance
            issues.append(
                issue(
                    "GPIO_NO_OUTPUT",
                    f"GPIO{gpio} cannot drive an output for {who}.",
                    part_ids=involved,
                    fix_hint="Use an output-capable GPIO.",
                )
            )

    return issues
