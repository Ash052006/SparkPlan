"""V6 I2C: I2C parts share SDA and SCL; duplicate addresses are an error.

All I2C parts on the bus must use a single SDA GPIO and a single SCL GPIO. Two
parts sharing the same I2C address cannot coexist on one bus.
"""

from __future__ import annotations

from sparkplan.models.buildspec import SupportedBuild
from sparkplan.models.catalog import BoardModel, Catalog, Interface
from sparkplan.models.spec import Issue
from sparkplan.validators.phase2.pins_check import _controller_gpio
from sparkplan.validators.phase2.report import issue


def validate_i2c(spec: SupportedBuild, catalog: Catalog, board: BoardModel) -> list[Issue]:
    issues: list[Issue] = []

    sda_gpios: dict[int, list[str]] = {}
    scl_gpios: dict[int, list[str]] = {}
    addr_users: dict[str, list[str]] = {}

    for conn in spec.connections:
        part = (
            catalog.get_or_none(spec.part_on(conn.from_instance))
            if spec.instance(conn.from_instance)
            else None
        )
        if part is None or part.interface is not Interface.I2C:
            continue
        gpio = _controller_gpio(conn.to_pin)
        if gpio is None:
            continue
        role = conn.signal_role
        pin = part.pin(conn.from_pin)
        if role == "sda" or (pin is not None and pin.role.value == "sda"):
            sda_gpios.setdefault(gpio, []).append(conn.from_instance)
        elif role == "scl" or (pin is not None and pin.role.value == "scl"):
            scl_gpios.setdefault(gpio, []).append(conn.from_instance)

    # address conflicts
    for part_ref in spec.parts:
        part = catalog.get_or_none(part_ref.part_id)
        if part is None or part.interface is not Interface.I2C or not part.i2c_address:
            continue
        addr_users.setdefault(part.i2c_address.upper(), []).append(part_ref.instance_id)

    for address, users in addr_users.items():
        if len(users) > 1:
            issues.append(
                issue(
                    "I2C_ADDRESS_CONFLICT",
                    f"I2C address {address} is used by {sorted(users)}; two devices "
                    f"cannot share an address on one bus.",
                    part_ids=sorted(users),
                    fix_hint="Change an address jumper or pick a part with a different address.",
                )
            )

    # bus must be a single SDA and a single SCL
    if len(sda_gpios) > 1:
        issues.append(
            issue(
                "I2C_BUS_SPLIT",
                f"I2C parts use multiple SDA lines: {sorted(sda_gpios)}.",
                part_ids=[i for ids in sda_gpios.values() for i in ids],
                fix_hint="Route every I2C part's SDA to the same GPIO (default D21).",
            )
        )
    if len(scl_gpios) > 1:
        issues.append(
            issue(
                "I2C_BUS_SPLIT",
                f"I2C parts use multiple SCL lines: {sorted(scl_gpios)}.",
                part_ids=[i for ids in scl_gpios.values() for i in ids],
                fix_hint="Route every I2C part's SCL to the same GPIO (default D22).",
            )
        )

    return issues
