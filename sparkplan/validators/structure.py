"""Structural checks: catalog membership, instances, buses, addresses."""

from __future__ import annotations

from sparkplan.catalog import Catalog
from sparkplan.models.spec import BuildSpec, Issue


def _err(code: str, message: str, part_ids: list[str] | None = None, hint: str = "") -> Issue:
    return Issue(
        code=code, severity="error", message=message, part_ids=part_ids or [], fix_hint=hint
    )


def _warn(code: str, message: str, part_ids: list[str] | None = None, hint: str = "") -> Issue:
    return Issue(
        code=code, severity="warning", message=message, part_ids=part_ids or [], fix_hint=hint
    )


def validate_structure(spec: BuildSpec, catalog: Catalog) -> list[Issue]:
    issues: list[Issue] = []

    if not spec.parts:
        issues.append(
            _err(
                "NO_PARTS",
                "BuildSpec lists no parts; an idea needs at least one catalog part.",
                hint="Pick catalog parts that realize the idea's capabilities.",
            )
        )

    seen: set[str] = set()
    for inst in spec.parts:
        if inst.instance_id in seen:
            issues.append(
                _err(
                    "DUPLICATE_INSTANCE",
                    f"instance_id {inst.instance_id!r} appears more than once.",
                    part_ids=[inst.part_id],
                    hint="Rename instances so each has a unique instance_id.",
                )
            )
        seen.add(inst.instance_id)
        if catalog.part_or_none(inst.part_id) is None:
            issues.append(
                _err(
                    "UNKNOWN_PART",
                    f"part_id {inst.part_id!r} is not in the catalog and may never be recommended.",
                    part_ids=[inst.part_id],
                    hint="Use only part_ids from the catalog; refuse the idea if nothing fits.",
                )
            )

    if spec.controller != catalog.power.controller:
        issues.append(
            _err(
                "UNKNOWN_CONTROLLER",
                f"controller {spec.controller!r} is not the catalog controller.",
                hint=f"The only supported controller is {catalog.power.controller}.",
            )
        )

    issues.extend(_check_buses(spec, catalog))
    issues.extend(_check_unwired_i2c_defaults(spec, catalog))
    return issues


def _check_buses(spec: BuildSpec, catalog: Catalog) -> list[Issue]:
    """All I2C parts must share one bus; all SPI parts share SCK/MOSI/MISO with distinct CS."""
    issues: list[Issue] = []

    i2c_conns: dict[str, list[tuple[str, str, str]]] = {"i2c_sda": [], "i2c_scl": []}
    spi_conns: dict[str, list[tuple[str, str, str]]] = {
        "spi_sck": [],
        "spi_mosi": [],
        "spi_miso": [],
        "spi_cs": [],
    }

    for conn in spec.connections:
        inst = spec.instance(conn.instance_id)
        if inst is None:
            continue
        part = catalog.part_or_none(inst.part_id)
        if part is None:
            continue
        pin = part.pin(conn.peripheral_pin)
        if pin is None:
            continue
        if pin.kind in i2c_conns:
            i2c_conns[pin.kind].append((conn.instance_id, conn.peripheral_pin, conn.controller_pin))
        if pin.kind in spi_conns:
            spi_conns[pin.kind].append((conn.instance_id, conn.peripheral_pin, conn.controller_pin))

    for kind, entries in i2c_conns.items():
        pins_used = {e[2] for e in entries}
        if len(pins_used) > 1:
            issues.append(
                _err(
                    "I2C_BUS_SPLIT",
                    f"I2C {kind} pins disagree across parts: {sorted(pins_used)}.",
                    part_ids=sorted({e[0] for e in entries}),
                    hint="Wire every I2C part to the same SDA/SCL GPIOs (defaults: GPIO21/GPIO22).",
                )
            )

    addrs: dict[str, list[str]] = {}
    for inst in spec.parts:
        part = catalog.part_or_none(inst.part_id)
        if part is None or part.firmware.i2c_address is None:
            continue
        addrs.setdefault(part.firmware.i2c_address, []).append(inst.instance_id)
    for addr, instances in addrs.items():
        if len(instances) > 1:
            conflict_parts: list[str] = []
            for iid in instances:
                inst_obj = spec.instance(iid)
                if inst_obj is not None:
                    conflict_parts.append(inst_obj.part_id)
            issues.append(
                _err(
                    "I2C_ADDRESS_CONFLICT",
                    f"I2C address {addr} used by multiple parts: {instances}.",
                    part_ids=conflict_parts,
                    hint="Remove one of the conflicting parts; addresses are fixed in the catalog.",
                )
            )

    cs_by_bus: dict[str, list[str]] = {}
    for instance_id, _pin, controller_pin in spi_conns["spi_cs"]:
        cs_by_bus.setdefault(controller_pin, []).append(instance_id)
    if len(cs_by_bus) > 1:
        issues.append(
            _err(
                "SPI_CS_CONFLICT",
                f"SPI chip-select GPIOs differ across parts: {sorted(cs_by_bus)}.",
                hint="Give each SPI part its own CS GPIO; keep SCK/MOSI/MISO shared.",
            )
        )
    for controller_pin, instances in cs_by_bus.items():
        if len(instances) > 1:
            issues.append(
                _err(
                    "SPI_CS_SHARED",
                    f"SPI parts {instances} share chip-select "
                    f"GPIO{controller_pin.removeprefix('GPIO')}.",
                    hint="Each SPI device needs its own CS line.",
                )
            )
    return issues


def _check_unwired_i2c_defaults(spec: BuildSpec, catalog: Catalog) -> list[Issue]:
    """Warn when an I2C part has no SDA/SCL connection at all."""
    issues: list[Issue] = []
    for inst in spec.parts:
        part = catalog.part_or_none(inst.part_id)
        if part is None:
            continue
        kinds = {p.kind for p in part.signal_pins()}
        if "i2c_sda" not in kinds:
            continue
        wired = {
            part.pin(c.peripheral_pin).kind
            for c in spec.connections
            if c.instance_id == inst.instance_id and part.pin(c.peripheral_pin) is not None
        }
        for required in ("i2c_sda", "i2c_scl"):
            if required not in wired:
                issues.append(
                    _err(
                        "I2C_PIN_UNCONNECTED",
                        f"I2C part {inst.instance_id!r} has no {required} connection.",
                        part_ids=[part.part_id],
                        hint=f"Connect {required} to the shared I2C GPIOs.",
                    )
                )
    return issues
