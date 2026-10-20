"""V3 conflicts: one GPIO drives one signal, except the shared I2C bus lines.

Signals on the same GPIO conflict unless they are I2C: any number of parts may
share the SDA line and the SCL line (one GPIO each).
"""

from __future__ import annotations

from sparkplan.models.buildspec import SupportedBuild
from sparkplan.models.catalog import BoardModel, Catalog
from sparkplan.models.spec import Issue
from sparkplan.validators.phase2.pins_check import _controller_gpio
from sparkplan.validators.phase2.report import issue

_SHARED_ROLES = {"sda", "scl", "rail", "gnd", "passive"}


def validate_conflicts(spec: SupportedBuild, catalog: Catalog, board: BoardModel) -> list[Issue]:
    issues: list[Issue] = []

    # gpio -> set of instance ids claiming it as a non-shareable signal
    claims: dict[int, list[str]] = {}
    for conn in spec.connections:
        if conn.signal_role in _SHARED_ROLES:
            continue
        gpio = _controller_gpio(conn.to_pin)
        if gpio is None:
            continue
        claims.setdefault(gpio, []).append(conn.from_instance)

    for gpio, instances in claims.items():
        distinct = sorted(set(instances))
        if len(distinct) > 1:
            issues.append(
                issue(
                    "GPIO_CONFLICT",
                    f"GPIO{gpio} is claimed by {distinct}; one GPIO drives one signal "
                    f"(except the shared I2C bus).",
                    part_ids=distinct,
                    fix_hint="Move one signal to a different GPIO.",
                )
            )

    return issues
