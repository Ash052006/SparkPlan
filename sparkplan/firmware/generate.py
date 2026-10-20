"""Firmware generation from catalog templates.

The LLM never writes firmware. Sketches are assembled deterministically
from per-part templates in the catalog, with GPIOs taken from the
validated BuildSpec. Unresolved placeholders are hard errors.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sparkplan.catalog import Catalog
from sparkplan.models.spec import Action, BuildSpec, PartInstance


class FirmwareError(RuntimeError):
    """Template assembly failed (e.g. unresolved placeholder)."""


_COMPILE_TIMEOUT_S = 300


@dataclass(frozen=True)
class FirmwareResult:
    sketch: str
    libraries: list[str]
    action_vars: dict[str, str] = field(default_factory=dict)
    compile_status: Literal["ok", "failed", "skipped"] = "skipped"
    compile_log: str = ""

    @property
    def sketch_path_hint(self) -> str:
        return "sparkplan.ino"


def _sanitize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _gpio_map(spec: BuildSpec, inst: PartInstance, catalog: Catalog) -> dict[str, str]:
    """Placeholders for one instance: {'gpio': 'GPIO4', 'gpio_trig': 'GPIO13', ...}"""
    conns = [c for c in spec.connections if c.instance_id == inst.instance_id]
    mapping: dict[str, str] = {"instance": inst.instance_id}
    if len(conns) == 1:
        mapping["gpio"] = conns[0].controller_pin
    for c in conns:
        key = "gpio_" + _sanitize(c.peripheral_pin)
        mapping[key] = c.controller_pin
    return mapping


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


def _format_snippet(fmt: str) -> tuple[str, str, int]:
    """Convert 'T={value:.1f}C' into (c_format, buffer_size_expr_note, n_args)."""
    n_args = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal n_args
        n_args += 1
        spec = m.group(1)
        if not spec:
            return "%s"
        conv = {"f": "f", "d": "d", "s": "s", "g": "g", "x": "x", "02d": "02d", "03d": "03d"}
        py_conv = spec.split(":")[-1] if ":" in spec else "s"
        py_conv = py_conv.replace(".", "0").replace("d", "d").replace("f", "f")
        if py_conv not in conv and py_conv.replace("0", "d") not in conv:
            py_conv = "s"
        return f"%{py_conv}"

    c_fmt = re.sub(r"\{value(?::([^}]+))?\}", repl, fmt)
    buf = max(len(fmt) + 16, 32)
    return c_fmt, f"{buf}", n_args


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
            c_fmt, buf_size, n_args = _format_snippet(fmt)
            lines.append(f"  char {var}[{buf_size}];")
            lines.append(f'  snprintf({var}, sizeof({var}), "{c_fmt}", {src});')
            assert n_args >= 1
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


def generate_firmware(spec: BuildSpec, catalog: Catalog) -> FirmwareResult:
    includes: list[str] = []
    defines: list[str] = []
    globals_: list[str] = []
    setup_lines: list[str] = ["Serial.begin(115200);"]
    functions: list[str] = []
    libraries: list[str] = []
    action_vars: dict[str, str] = {}

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
        "// Generated by SparkPlan. Do not hand-edit; change the catalog template instead.",
        f"// Idea: {spec.idea}",
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


def write_sketch(result: FirmwareResult, directory: str | Path) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / result.sketch_path_hint
    path.write_text(result.sketch)
    return path


def compile_firmware(
    result: FirmwareResult,
    directory: str | Path,
    cli_path: str | None = None,
    extra_lib_install: bool = True,
) -> FirmwareResult:
    """Compile with arduino-cli when available; otherwise mark skipped."""
    import os
    import shutil
    import tempfile

    cli = cli_path or os.environ.get("SPARKPLAN_ARDUINO_CLI") or shutil.which("arduino-cli")
    if not cli:
        return FirmwareResult(
            sketch=result.sketch,
            libraries=result.libraries,
            action_vars=result.action_vars,
            compile_status="skipped",
            compile_log="arduino-cli not found; install it or set SPARKPLAN_ARDUINO_CLI",
        )

    sketch_dir = Path(directory)
    write_sketch(result, sketch_dir)
    log_parts: list[str] = []

    if extra_lib_install and result.libraries:
        install = subprocess.run(  # noqa: S603
            [cli, "lib", "install", *result.libraries],
            capture_output=True,
            text=True,
            timeout=_COMPILE_TIMEOUT_S,
        )
        log_parts.append(install.stdout + install.stderr)

    with tempfile.TemporaryDirectory() as build_dir:
        proc = subprocess.run(  # noqa: S603
            [
                cli,
                "compile",
                "--fqbn",
                "esp32:esp32:esp32",
                "--build-path",
                build_dir,
                str(sketch_dir),
            ],
            capture_output=True,
            text=True,
            timeout=_COMPILE_TIMEOUT_S,
        )
        log_parts.append(proc.stdout + proc.stderr)
        status = "ok" if proc.returncode == 0 else "failed"

    return FirmwareResult(
        sketch=result.sketch,
        libraries=result.libraries,
        action_vars=result.action_vars,
        compile_status=status,
        compile_log="\n".join(log_parts)[-8000:],
    )
