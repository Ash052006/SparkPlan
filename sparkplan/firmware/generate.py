"""Firmware generation from catalog templates.

The LLM never writes firmware. Sketches are assembled deterministically
from per-part templates in the catalog, with GPIOs taken from the
validated BuildSpec. Unresolved placeholders are hard errors.

Two spec shapes are supported:

* ``sparkplan.models.spec.BuildSpec`` with actions and the split catalog
  (``catalog/parts/*.json``) - the legacy action pipeline, unchanged.
* ``sparkplan.models.buildspec.SupportedBuild`` with behaviours and the
  Phase 2+ catalog (``catalog/parts.json``) - see
  :mod:`sparkplan.firmware.behaviors`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sparkplan.catalog import Catalog
from sparkplan.firmware.behaviors import (
    BehaviorFirmware,
    generate_behavior_firmware,
    unsupported_error,
)
from sparkplan.firmware.context import make_context
from sparkplan.firmware.errors import FirmwareError, FirmwareUnsupportedError
from sparkplan.firmware.template import canonical_hash, canonical_payload
from sparkplan.models.buildspec import SupportedBuild
from sparkplan.models.catalog import Catalog as CatalogV2
from sparkplan.models.spec import Action, BuildSpec, Issue, PartInstance

__all__ = [
    "FirmwareError",
    "FirmwareResult",
    "FirmwareUnsupportedError",
    "compile_firmware",
    "generate_firmware",
    "write_sketch",
]


@dataclass(frozen=True)
class FirmwareResult:
    sketch: str
    libraries: list[str]
    action_vars: dict[str, str] = field(default_factory=dict)
    compile_status: Literal["ok", "failed", "skipped"] = "skipped"
    compile_log: str = ""
    readings: dict[str, str] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    @property
    def sketch_path_hint(self) -> str:
        return "sparkplan.ino"


def _sanitize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _gpio_number(catalog: Catalog, controller_pin: str) -> int | None:
    """The GPIO number of a legacy board pin label.

    The legacy catalog names the broken out pins of the controller
    ``GPIO4``, ``GPIO13`` and so on, and the legacy pin validator derives the
    number the same way.  The ESP32 core has no ``GPIO4`` symbol, so the
    firmware has to use the number: ``DHT env_dht(4, DHTTYPE);``.  The
    number is taken from the catalog pin, never guessed.
    """
    pin = catalog.controller.pin(controller_pin)
    if pin is not None and pin.gpio is not None:
        return pin.gpio
    cleaned = controller_pin.strip().upper()
    if cleaned.startswith("GPIO") and cleaned[4:].isdigit():
        return int(cleaned[4:])
    return None


def _gpio_map(spec: BuildSpec, inst: PartInstance, catalog: Catalog) -> dict[str, str]:
    """Placeholders for one instance: {'gpio': '4', 'gpio_trig': '13', ...}"""
    conns = [c for c in spec.connections if c.instance_id == inst.instance_id]
    mapping: dict[str, str] = {"instance": inst.instance_id}
    if len(conns) == 1:
        mapping["gpio"] = _gpio_expression(catalog, conns[0].controller_pin, inst.instance_id)
    for c in conns:
        key = "gpio_" + _sanitize(c.peripheral_pin)
        mapping[key] = _gpio_expression(catalog, c.controller_pin, inst.instance_id)
    return mapping


def _gpio_expression(catalog: Catalog, controller_pin: str, instance_id: str) -> str:
    """The C++ expression of a board pin: the GPIO number."""
    number = _gpio_number(catalog, controller_pin)
    if number is None:
        raise FirmwareError(
            f"board pin {controller_pin!r} of instance {instance_id!r} has no GPIO number"
        )
    return str(number)


def _subst(line: str, mapping: dict[str, str], instance_id: str) -> str:
    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in mapping:
            raise FirmwareError(
                f"unresolved template placeholder {{{key}}} for instance {instance_id!r} "
                f"(known: {sorted(mapping)})"
            )
        return mapping[key]

    return re.sub(r"\{([a-z_]+)\}", repl, line)


def _cpp_args(action: Action, api_param: str | None) -> list[str]:
    """Build call arguments from consumes + params."""
    args: list[str] = []
    params = action.params or {}
    if api_param is None:
        return args
    declared = [p.strip() for p in api_param.split(",") if p.strip()]
    consume_first = action.consumes is not None
    if consume_first:
        declared = declared[1:]  # consumed value fills the first parameter
    if len(declared) != len(params) and params:
        # Tolerate extra metadata keys; take values in declaration order.
        values = list(params.values())[: len(declared)]
    else:
        values = list(params.values())
    for v in values:
        if isinstance(v, bool):
            args.append("true" if v else "false")
        elif isinstance(v, (int, float)):
            args.append(repr(float(v)) if isinstance(v, float) else str(v))
        elif isinstance(v, str):
            args.append(json.dumps(v))
        else:
            args.append(json.dumps(v))
    return args


# C conversions that take a floating point argument.
_FLOAT_CONVERSIONS = "feEgG"
# C conversions that take an integer argument.
_INT_CONVERSIONS = "diuxXo"


def _escape_c_literal(text: str) -> str:
    """Escape the literal part of a format string for C."""
    return text.replace("%", "%%").replace('"', '\\"')


def _default_conversion(value_type: str) -> str:
    """The natural printf conversion for a value of the given C++ type."""
    if value_type in {"float", "double"}:
        return "%g"
    if value_type in {"int", "long", "unsigned", "unsigned long", "short"}:
        return "%d"
    if value_type in {"bool"}:
        return "%s"
    return "%s"


def _format_snippet(fmt: str, value_type: str, value_expr: str) -> tuple[str, str]:
    """Convert ``'T={value:.1f}C'`` into a C format and its argument expression.

    The conversion always matches the type of the value, so a float is never
    handed to a ``%s`` conversion (which is undefined behaviour) and an int is
    never handed to a ``%f`` conversion.
    """
    match = re.search(r"\{value(?::([^}]+))?\}", fmt)
    if match is None:
        return _escape_c_literal(fmt), value_expr
    spec = (match.group(1) or "").strip()
    literal_before = _escape_c_literal(fmt[: match.start()])
    literal_after = _escape_c_literal(fmt[match.end() :])

    conversion = ""
    precision = re.fullmatch(r"\.(\d+)([a-zA-Z])", spec)
    zero_padded = re.fullmatch(r"0(\d+)([diouxX])", spec)
    single = re.fullmatch(r"([a-zA-Z])", spec)
    if precision is not None:
        conversion = f"%.{precision.group(1)}{precision.group(2)}"
    elif zero_padded is not None:
        conversion = f"%0{zero_padded.group(1)}{zero_padded.group(2)}"
    elif single is not None:
        conversion = f"%{single.group(1)}"
    else:
        conversion = _default_conversion(value_type)

    is_float_value = value_type in {"float", "double"}
    is_int_value = value_type in {"int", "long", "unsigned", "unsigned long", "short"}
    is_bool_value = value_type == "bool"

    conv_char = conversion[-1]
    if conv_char in _FLOAT_CONVERSIONS:
        arg = f"(double){value_expr}" if not is_float_value else value_expr
    elif conv_char in _INT_CONVERSIONS:
        arg = f"(int){value_expr}" if is_float_value else value_expr
    elif conv_char == "s":
        if is_bool_value:
            arg = f'({value_expr} ? "true" : "false")'
        elif is_float_value or is_int_value:
            # A numeric value printed as a string: convert it explicitly.
            arg = f"String({value_expr}).c_str()"
        else:
            arg = value_expr
    else:
        conversion = _default_conversion(value_type)
        arg = value_expr
    return f"{literal_before}{conversion}{literal_after}", arg


def _source_type(spec: BuildSpec, catalog: Catalog, action_id: str) -> str:
    """The C++ type of the value produced by another action."""
    for other in spec.actions:
        if other.action_id != action_id:
            continue
        inst = spec.instance(other.instance_id)
        if inst is None:
            return ""
        api = catalog.api_fn(inst.part_id, other.api)
        if api is None:
            return ""
        return api.returns
    return ""


def _render_action(action: Action, spec: BuildSpec, catalog: Catalog, var: str) -> list[str]:
    inst = spec.instance(action.instance_id)
    if inst is None:
        raise FirmwareError(f"action {action.action_id} references unknown instance")
    api = catalog.api_fn(inst.part_id, action.api)
    if api is None:
        raise FirmwareError(f"unknown api {action.api!r} for {inst.part_id}")
    call = api.fn.replace("{instance}", inst.instance_id)

    lines: list[str] = []
    args = _cpp_args(action, api.param)
    if action.consumes is not None:
        src = f"v_{action.consumes}"
        fmt = (action.params or {}).get("format")
        if isinstance(fmt, str) and "{value" in fmt:
            c_fmt, arg_expr = _format_snippet(
                fmt, _source_type(spec, catalog, action.consumes), src
            )
            buf = max(len(fmt) + 16, 32)
            lines.append(f"  char {var}[{buf}];")
            lines.append(f'  snprintf({var}, sizeof({var}), "{c_fmt}", {arg_expr});')
            args = [var, *args]
        else:
            # Coerce integer sources to float where the parameter expects float.
            first_param = (api.param or "").split(",")[0].strip()
            expr = f"(float){src}" if first_param.startswith("float") else src
            args = [expr, *args]
    if action.kind == "read":
        lines.append(f"  auto {var} = {call}({', '.join(args)});")
    else:
        lines.append(f"  {call}({', '.join(args)});")
    return lines


def _legacy_header(spec: BuildSpec, ctx: object) -> list[str]:
    """Deterministic header comment: components, GPIOs and the spec hash."""
    digest = canonical_hash(canonical_payload(spec))
    instances = sorted(ctx.instances.items())  # type: ignore[attr-defined]
    connections = sorted(
        ctx.connections,  # type: ignore[attr-defined]
        key=lambda c: (c.instance_id, c.peripheral_pin, c.controller_pin),
    )
    lines = [
        "// Generated by SparkPlan. Do not hand-edit; change the catalog template instead.",
        f"// Idea: {ctx.idea}",  # type: ignore[attr-defined]
        "// Components:",
    ]
    for instance_id, instance in instances:
        lines.append(f"//   {instance_id} = {instance.part_id}")
    lines.append("// GPIO assignments:")
    for connection in connections:
        lines.append(
            f"//   {connection.instance_id}.{connection.peripheral_pin} -> "
            f"{connection.controller_pin}"
        )
    lines.append(f"// Spec hash (sha256/16): {digest}")
    return lines


def generate_firmware(
    spec: BuildSpec | SupportedBuild, catalog: Catalog | CatalogV2
) -> FirmwareResult:
    """Render a sketch for a build spec.

    A ``SupportedBuild`` is rendered by :mod:`sparkplan.firmware.behaviors`
    and needs the Phase 2+ catalog; a legacy ``BuildSpec`` is rendered by the
    action pipeline and needs the split catalog.
    """
    if isinstance(spec, SupportedBuild):
        if not isinstance(catalog, CatalogV2):
            raise FirmwareError(
                "a SupportedBuild needs the Phase 2+ catalog (catalog/parts.json), "
                f"got {type(catalog).__name__}"
            )
        behaviour_firmware: BehaviorFirmware = generate_behavior_firmware(spec, catalog)
        if not behaviour_firmware.supported:
            raise unsupported_error(behaviour_firmware)
        return FirmwareResult(
            sketch=behaviour_firmware.sketch,
            libraries=behaviour_firmware.libraries,
            readings=behaviour_firmware.readings,
            issues=behaviour_firmware.issues,
        )
    if not isinstance(spec, BuildSpec):
        raise FirmwareError(f"unsupported spec type for firmware: {type(spec).__name__}")
    if not isinstance(catalog, Catalog):
        raise FirmwareError(
            "a legacy BuildSpec needs the split catalog (catalog/parts/*.json), "
            f"got {type(catalog).__name__}"
        )
    # Normalize via context (preserves behavior for legacy specs)
    ctx = make_context(spec)
    includes: list[str] = []
    defines: list[str] = []
    globals_: list[str] = []
    setup_lines: list[str] = ["Serial.begin(115200);"]
    functions: list[str] = []
    libraries: list[str] = []
    action_vars: dict[str, str] = {}
    header_lines = _legacy_header(spec, ctx)

    def _extend(target: list[str], items: list[str]) -> None:
        for item in items:
            if item not in target:
                target.append(item)

    for inst in spec.parts:
        part = catalog.part_or_none(inst.part_id)
        if part is None:
            raise FirmwareError(f"unknown part {inst.part_id!r}")
        if not part.firmware.api and not part.firmware.setup:
            continue  # controller / pure-power parts contribute nothing here
        mapping = _gpio_map(spec, inst, catalog)
        fw = part.firmware
        _extend(libraries, fw.libraries)
        _extend(includes, [_subst(x, mapping, inst.instance_id) for x in fw.includes])
        _extend(defines, [_subst(x, mapping, inst.instance_id) for x in fw.defines])
        _extend(globals_, [_subst(x, mapping, inst.instance_id) for x in fw.globals])
        _extend(setup_lines, [_subst(x, mapping, inst.instance_id) for x in fw.setup])
        _extend(functions, [_subst(x, mapping, inst.instance_id) for x in fw.functions])

    loop_lines: list[str] = []
    for action in spec.actions:
        var = f"v_{action.action_id}"
        action_vars[action.action_id] = var
        loop_lines.extend(_render_action(action, spec, catalog, var))
    loop_lines.append("  delay(200);")

    sketch_lines = [
        *header_lines,
        "",
        *includes,
        "",
        *defines,
        "",
        *globals_,
        "",
        *functions,
        "",
        "void setup() {",
        *[f"  {line}" for line in setup_lines],
        "}",
        "",
        "void loop() {",
        *loop_lines,
        "}",
        "",
    ]
    return FirmwareResult(
        sketch="\n".join(sketch_lines), libraries=libraries, action_vars=action_vars
    )


def _sketch_filename(name: str | None, default: str) -> str:
    filename = name or default
    if (
        not filename.endswith(".ino")
        or Path(filename).name != filename
        or filename in {".ino", "..ino"}
    ):
        raise FirmwareError(f"invalid sketch filename: {filename!r}")
    return filename


def write_sketch(
    result: FirmwareResult,
    directory: str | Path,
    filename: str | None = None,
    *,
    sketch_name: str | None = None,
) -> Path:
    """Write the sketch, defaulting to the legacy ``sparkplan.ino`` name."""
    if filename is not None and sketch_name is not None and filename != sketch_name:
        raise ValueError("filename and sketch_name disagree")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _sketch_filename(sketch_name or filename, result.sketch_path_hint)
    path.write_text(result.sketch)
    return path


def compile_firmware(
    result: FirmwareResult,
    directory: str | Path,
    cli_path: str | None = None,
    extra_lib_install: bool = True,
    *,
    sketch_name: str | None = None,
    write_sketch_file: bool = True,
) -> FirmwareResult:
    """Compile with arduino-cli when available; otherwise mark skipped.
    Delegates to compile module for consistency while preserving API.

    ``sketch_name`` selects the primary ``.ino`` filename. The default stays
    ``sparkplan.ino`` for legacy callers. ``write_sketch_file=False`` is for
    callers that already persisted the sketch exclusively (for example the
    canonical run pipeline).
    """

    from sparkplan.firmware.compile import compile_sketch
    from sparkplan.firmware.manifest import resolve_libraries, write_manifest

    sketch_dir = Path(directory)
    filename = _sketch_filename(sketch_name, result.sketch_path_hint)
    if write_sketch_file:
        write_sketch(result, sketch_dir, filename=filename)
    resolution = resolve_libraries(result.libraries)
    if resolution.unknown:
        issues = [
            *result.issues,
            *[
                Issue(
                    code="FIRMWARE_UNPINNED_LIBRARY",
                    severity="error",
                    message=f"library {name!r} is not pinned in the manifest",
                    part_ids=[],
                    fix_hint="Pin the library in sparkplan/firmware/manifest.py.",
                )
                for name in resolution.unknown
            ],
        ]
        return FirmwareResult(
            sketch=result.sketch,
            libraries=result.libraries,
            action_vars=result.action_vars,
            compile_status="skipped",
            compile_log="",
            readings=result.readings,
            issues=issues,
        )
    # The pinned manifest is written next to the sketch so the exact library
    # versions of a build are recorded with the build.
    write_manifest(sketch_dir, result.libraries)
    artifacts = compile_sketch(
        sketch_dir,
        fqbn="esp32:esp32:esp32",
        cli_path=cli_path,
        libraries=list(resolution.specs) if extra_lib_install else None,
        extra_lib_install=extra_lib_install,
    )

    # A zero exit status is necessary but not sufficient: verify the compile
    # result and the sketch artifact that this wrapper is responsible for.
    verification_issues: list[Issue] = []
    if artifacts.status == "passed":
        if artifacts.exit_code not in (None, 0):
            verification_issues.append(
                Issue(
                    code="COMPILE_EXIT_CODE_MISMATCH",
                    severity="error",
                    message=(
                        "arduino-cli reported success but exited with code "
                        f"{artifacts.exit_code}"
                    ),
                    part_ids=[],
                    fix_hint="Inspect the arduino-cli output",
                )
            )
        required_sketch = sketch_dir / filename
        if (
            artifacts.sketch_path is None
            or not Path(artifacts.sketch_path).is_dir()
            or not required_sketch.is_file()
            or required_sketch.stat().st_size == 0
        ):
            verification_issues.append(
                Issue(
                    code="COMPILE_OUTPUT_MISSING",
                    severity="error",
                    message=(
                        "arduino-cli reported success but the required sketch "
                        f"output is missing or empty: {required_sketch}"
                    ),
                    part_ids=[],
                    fix_hint="Check the arduino-cli build output and sketch path",
                )
            )

    # Map to old status
    if artifacts.status == "passed" and not verification_issues:
        status = "ok"
    elif artifacts.status == "skipped":
        status = "skipped"
    else:  # failed/unsupported, or a failed output check
        status = "failed"
    issues = [*result.issues, *artifacts.issues, *verification_issues]
    return FirmwareResult(
        sketch=result.sketch,
        libraries=result.libraries,
        action_vars=result.action_vars,
        compile_status=status,
        compile_log=artifacts.logs[-8000:],
        readings=result.readings,
        issues=issues,
    )
