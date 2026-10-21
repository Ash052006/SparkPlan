"""Firmware generation for builds that describe behaviour instead of actions.

A :class:`~sparkplan.models.buildspec.SupportedBuild` says what the build
should *do* (readings, rules, display lines) while the catalog says how each
part is driven.  This module turns both into a sketch without inventing
hardware semantics:

* every reading is a variable named ``<instance_id>_<reading_name>`` and is
  filled from the part's own ``read_snippet``;
* a reading that the catalog cannot produce is an explicit error, never a
  fabricated value;
* sampling is scheduled with ``millis()`` and unsigned arithmetic, so the
  timer can roll over safely;
* a reading that could not be read keeps its last valid value and is marked
  invalid, and rules only fire on valid readings;
* rules compile to explicit ``if`` conditions that fire on the rising edge,
  so a condition that stays true does not repeat its action;
* a rule that targets an actuator is reported as unsupported: the schema says
  *which* actuator, but not which action or with which argument, and guessing
  that would invent hardware behaviour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sparkplan.firmware.errors import FirmwareUnsupportedError
from sparkplan.firmware.template import (
    ActionTemplate,
    InstanceTemplate,
    RenderedTemplate,
    TemplateEngine,
    canonical_hash,
    canonical_payload,
)
from sparkplan.models.buildspec import Behavior, DisplayLine, Rule, SupportedBuild
from sparkplan.models.catalog import Catalog
from sparkplan.models.spec import Issue

# Default sampling tick.  The build schema has no interval field, so the
# generator uses one documented tick for every instance; parts whose protocol
# needs a slower rate (the DHT library, for example) enforce that themselves
# and return an invalid reading until the rate is allowed.
DEFAULT_TICK_MS = 200

# Action a display part must expose so a display line can be rendered.
DISPLAY_ACTION = "show_line"

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class BehaviorFirmware:
    """Result of rendering a behaviour based build.

    ``supported`` is false when the build cannot be rendered without
    inventing semantics; ``issues`` then explains why.
    """

    sketch: str
    libraries: list[str] = field(default_factory=list)
    readings: dict[str, str] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    supported: bool = True

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]


@dataclass(frozen=True)
class _ResolvedReading:
    """A reading the firmware can produce, with the instance that owns it."""

    name: str
    unit: str
    cpp_type: str
    var: str
    instance_id: str
    valid_var: str


def _slug(name: str) -> str:
    """A C++ identifier fragment for a human readable name."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name).strip("_").lower()
    if not cleaned:
        cleaned = "rule"
    if cleaned[0].isdigit():
        cleaned = f"r_{cleaned}"
    return cleaned


def _value_expression(reading: _ResolvedReading) -> str:
    """C++ expression that prints the value of a reading."""
    if reading.cpp_type == "bool":
        return f'({reading.var} ? "true" : "false")'
    if reading.cpp_type == "int":
        return f"(int){reading.var}"
    return f"(double){reading.var}"


def _value_format(reading: _ResolvedReading) -> str:
    """printf conversion for a reading value."""
    if reading.cpp_type == "bool":
        return "%s"
    if reading.cpp_type == "int":
        return "%d"
    return "%.2f"


def _condition(reading: _ResolvedReading, rule: Rule) -> str | None:
    """C++ condition for a rule, or None when the rule cannot be expressed."""
    if reading.cpp_type == "bool":
        if rule.comparator not in {"==", "!="}:
            return None
        if rule.threshold not in (0.0, 1.0):
            return None
        wants_true = (rule.comparator == "==") == (rule.threshold == 1.0)
        return reading.var if wants_true else f"!{reading.var}"
    if reading.cpp_type == "int":
        return f"((double){reading.var} {rule.comparator} {rule.threshold!r})"
    return f"((double){reading.var} {rule.comparator} {rule.threshold!r})"


def _cpp_escape(text: str) -> str:
    """Escape text that is embedded in a C++ string literal used as a format.

    ``%`` becomes ``%%`` so a unit such as ``%RH`` is printed literally and
    cannot turn into a conversion specifier.
    """
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("%", "%%")


def _display_text(line: DisplayLine) -> str:
    """The escaped label of a display line, without the value."""
    if not line.text:
        return ""
    return f"{_cpp_escape(line.text)}:"


def _display_format(line: DisplayLine, reading: _ResolvedReading) -> str:
    """printf format of a display line."""
    fmt = _value_format(reading)
    prefix = _display_text(line)
    unit = _cpp_escape(line.unit)
    if prefix and unit:
        return f"{prefix} {fmt} {unit}"
    if prefix:
        return f"{prefix} {fmt}"
    if unit:
        return f"{fmt} {unit}"
    return fmt


class _Builder:
    """Assemble the sketch of one behaviour based build."""

    def __init__(self, spec: SupportedBuild, catalog: Catalog, tick_ms: int) -> None:
        self.spec = spec
        self.catalog = catalog
        self.tick_ms = tick_ms
        self.engine = TemplateEngine(spec, catalog)
        self.rendered: RenderedTemplate = self.engine.render()
        self.issues: list[Issue] = [issue.to_issue() for issue in self.rendered.issues]
        self._readings: list[_ResolvedReading] = []
        self._by_name: dict[str, _ResolvedReading] = {}
        self._by_var: dict[str, _ResolvedReading] = {}

    # -- issue helpers --------------------------------------------------
    def _error(
        self, code: str, message: str, part_ids: list[str] | None = None, fix_hint: str = ""
    ) -> None:
        self.issues.append(
            Issue(
                code=code,
                severity="error",
                message=message,
                part_ids=list(part_ids or []),
                fix_hint=fix_hint,
            )
        )

    def _warning(
        self, code: str, message: str, part_ids: list[str] | None = None, fix_hint: str = ""
    ) -> None:
        self.issues.append(
            Issue(
                code=code,
                severity="warning",
                message=message,
                part_ids=list(part_ids or []),
                fix_hint=fix_hint,
            )
        )

    # -- readings -------------------------------------------------------
    def _collect_readings(self) -> None:
        """Resolve every reading the build can produce."""
        seen_vars: dict[str, str] = {}
        for instance in self.rendered.instances:
            for reading in instance.readings:
                resolved = _ResolvedReading(
                    name=reading.reading_name,
                    unit=reading.unit,
                    cpp_type=reading.cpp_type,
                    var=reading.var,
                    instance_id=instance.instance_id,
                    valid_var=f"{reading.var}_valid",
                )
                if resolved.var in seen_vars:
                    self._error(
                        "FIRMWARE_DUPLICATE_READING",
                        f"reading variable {resolved.var!r} is declared twice",
                        [instance.part_id],
                        "Rename one of the readings.",
                    )
                    continue
                seen_vars[resolved.var] = resolved.var
                self._readings.append(resolved)
                self._by_var[resolved.var] = resolved
                if reading.reading_name in self._by_name:
                    other = self._by_name[reading.reading_name]
                    self._error(
                        "FIRMWARE_AMBIGUOUS_READING",
                        f"reading {reading.reading_name!r} is provided by more than one "
                        f"instance ({other.instance_id!r} and {instance.instance_id!r})",
                        [instance.part_id],
                        "Name the reading per instance in the behaviour.",
                    )
                    continue
                self._by_name[reading.reading_name] = resolved

        # Readings a behaviour declares that the catalog cannot produce.
        for behavior in self.spec.behaviors:
            for reading in behavior.readings:
                if not reading.instance_id:
                    self._error(
                        "FIRMWARE_UNKNOWN_READING",
                        f"behaviour {behavior.name!r} declares reading "
                        f"{reading.name!r} without an instance",
                        [],
                        "Bind the reading to the instance that produces it.",
                    )
                    continue
                instance = self.spec.instance(reading.instance_id)
                if instance is None:
                    self._error(
                        "FIRMWARE_UNKNOWN_READING",
                        f"behaviour {behavior.name!r} declares reading "
                        f"{reading.name!r} on unknown instance {reading.instance_id!r}",
                        [],
                        "Use an instance from the build spec.",
                    )
                    continue
                known = self._var_for(instance.instance_id, reading.name)
                if known is None:
                    self._error(
                        "FIRMWARE_UNKNOWN_READING",
                        f"instance {instance.instance_id!r} ({instance.part_id}) does not "
                        f"provide reading {reading.name!r}",
                        [instance.part_id],
                        "Use a reading the catalog part exposes.",
                    )
                    continue
                if reading.unit and known.unit and reading.unit != known.unit:
                    self._error(
                        "FIRMWARE_UNIT_MISMATCH",
                        f"reading {known.name!r} is {known.unit!r} but the behaviour "
                        f"declares {reading.unit!r}",
                        [instance.part_id],
                        "Use the unit the catalog declares.",
                    )

    def _var_for(self, instance_id: str, reading_name: str) -> _ResolvedReading | None:
        for reading in self._readings:
            if reading.instance_id == instance_id and reading.name == reading_name:
                return reading
        return None

    def _resolve(self, name: str) -> _ResolvedReading | None:
        return self._by_name.get(name)

    # -- rules ----------------------------------------------------------
    def _rule_lines(self, behavior: Behavior, rule: Rule) -> list[str]:
        """Render one rule as an explicit, edge triggered if-statement."""
        condition_reading = self._resolve(rule.condition_reading)
        if condition_reading is None:
            self._error(
                "FIRMWARE_UNKNOWN_READING",
                f"rule {rule.name!r} conditions on unknown reading " f"{rule.condition_reading!r}",
                [],
                "Use a reading the build produces.",
            )
            return []
        if (
            rule.condition_unit
            and condition_reading.unit
            and (rule.condition_unit != condition_reading.unit)
        ):
            self._error(
                "FIRMWARE_UNIT_MISMATCH",
                f"rule {rule.name!r} compares {condition_reading.name!r} as "
                f"{rule.condition_unit!r} but the reading is {condition_reading.unit!r}",
                [],
                "Use the unit the reading is measured in.",
            )
            return []
        if rule.action_actuator:
            self._error(
                "FIRMWARE_UNSUPPORTED_RULE_ACTION",
                f"rule {rule.name!r} drives actuator {rule.action_actuator!r} but the "
                "build schema does not say which action or with which argument",
                [],
                "Describe the action as a display line, or extend the schema with an "
                "explicit action.",
            )
            return []
        if not rule.action_reading:
            self._error(
                "FIRMWARE_UNSUPPORTED_RULE_ACTION",
                f"rule {rule.name!r} has no action",
                [],
                "Set action_reading or action_actuator.",
            )
            return []
        action_reading = self._resolve(rule.action_reading)
        if action_reading is None:
            self._error(
                "FIRMWARE_UNKNOWN_READING",
                f"rule {rule.name!r} acts on unknown reading {rule.action_reading!r}",
                [],
                "Use a reading the build produces.",
            )
            return []
        condition = _condition(condition_reading, rule)
        if condition is None:
            self._error(
                "FIRMWARE_UNSUPPORTED_RULE_ACTION",
                f"rule {rule.name!r} compares a {condition_reading.cpp_type} reading with "
                f"{rule.comparator!r} {rule.threshold}",
                [],
                "Compare a boolean reading with == or != 0/1 only.",
            )
            return []
        slug = _slug(rule.name)
        action_value = _value_expression(action_reading)
        action_format = _value_format(action_reading)
        return [
            f"  // rule '{rule.name}': {condition_reading.name} {rule.comparator} "
            f"{rule.threshold} ({condition_reading.unit or 'no unit'})",
            "  {",
            f"    const bool rule_{slug}_now = {condition_reading.valid_var} && " f"({condition});",
            f"    if (rule_{slug}_now && !rule_{slug}_active) {{",
            f'      Serial.printf("rule {_cpp_escape(rule.name)} fired: '
            f'{_cpp_escape(action_reading.name)}={action_format}\\n", {action_value});',
            "    }",
            f"    rule_{slug}_active = rule_{slug}_now;",
            "  }",
        ]

    def _rule_state(self, behavior: Behavior, rule: Rule) -> str:
        return f"static bool rule_{_slug(rule.name)}_active = false;"

    # -- display --------------------------------------------------------
    def _display_instance(self, target_instance: str) -> InstanceTemplate | None:
        for instance in self.rendered.instances:
            if instance.instance_id == target_instance:
                return instance
        return None

    def _display_action(self, instance: InstanceTemplate) -> ActionTemplate | None:
        for action in instance.actions:
            if action.name == DISPLAY_ACTION:
                return action
        return None

    def _display_lines(self, behavior: Behavior) -> list[str]:
        """Render the display lines of one behaviour."""
        lines: list[str] = []
        targets: dict[str, list[tuple[DisplayLine, _ResolvedReading]]] = {}
        order: list[str] = []
        for line in behavior.display_lines:
            instance = self._display_instance(line.target_instance)
            if instance is None:
                self._error(
                    "FIRMWARE_UNKNOWN_DISPLAY",
                    f"display line targets unknown instance {line.target_instance!r}",
                    [],
                    "Target an instance that is part of the build.",
                )
                continue
            action = self._display_action(instance)
            if action is None:
                self._error(
                    "FIRMWARE_UNSUPPORTED_DISPLAY",
                    f"part {instance.part_id!r} exposes no {DISPLAY_ACTION!r} action, so "
                    "display lines cannot be rendered on it",
                    [instance.part_id],
                    "Use a display part with a show_line action.",
                )
                continue
            reading = self._resolve(line.reading)
            if reading is None:
                self._error(
                    "FIRMWARE_UNKNOWN_READING",
                    f"display line references unknown reading {line.reading!r}",
                    [],
                    "Use a reading the build produces.",
                )
                continue
            if line.unit and reading.unit and line.unit != reading.unit:
                self._error(
                    "FIRMWARE_UNIT_MISMATCH",
                    f"display line shows {reading.name!r} as {line.unit!r} but the reading "
                    f"is {reading.unit!r}",
                    [],
                    "Use the unit the reading is measured in.",
                )
                continue
            if line.target_instance not in targets:
                targets[line.target_instance] = []
                order.append(line.target_instance)
            targets[line.target_instance].append((line, reading))

        for target in order:
            instance = self._display_instance(target)
            action = self._display_action(instance) if instance is not None else None
            if instance is None or action is None:
                continue
            entries = targets[target]
            buffer = f"line_{_slug(target)}"
            size = f"sizeof({buffer}) - (size_t)(cursor - {buffer})"
            lines.append(f"  // display lines for {target}")
            lines.append("  {")
            lines.append(f"    char {buffer}[160];")
            lines.append(f"    char *cursor = {buffer};")
            for line, reading in entries:
                fmt = _display_format(line, reading)
                prefix = _display_text(line)
                na_text = f"{prefix} n/a" if prefix else "n/a"
                lines.append(f"    if ({reading.valid_var}) {{")
                lines.append(
                    f'      snprintf(cursor, {size}, "{fmt}\\n", {_value_expression(reading)});'
                )
                lines.append("    } else {")
                lines.append(f'      snprintf(cursor, {size}, "{na_text}\\n");')
                lines.append("    }")
                lines.append("    cursor += strlen(cursor);")
            lines.append(f'    Serial.printf("%s", {buffer});')
            lines.append(f"    {self._instantiate_action(action, buffer)}")
            lines.append("  }")
        return lines

    def _instantiate_action(self, action: ActionTemplate, value: str) -> str:
        """Bind the action parameters to a value expression."""
        snippet = action.snippet
        for param in action.params:
            snippet = re.sub(rf"\b{re.escape(param)}\b", value, snippet)
        return snippet.replace("\n", "\n    ")

    # -- sketch ---------------------------------------------------------
    def _header(self) -> list[str]:
        payload = canonical_payload(self.spec)
        digest = canonical_hash(payload)
        lines = [
            "// Generated by SparkPlan. Do not hand-edit; change the catalog template instead.",
            f"// Idea: {self.spec.summary}",
            f"// Title: {self.spec.title}",
            "// Components:",
        ]
        for ref in sorted(self.spec.parts, key=lambda r: r.instance_id):
            lines.append(f"//   {ref.instance_id} = {ref.part_id}")
        lines.append("// GPIO assignments:")
        for assignment in self.rendered.gpio_assignments:
            lines.append(
                f"//   {assignment.instance_id}.{assignment.pin_name} -> "
                f"{assignment.board_pin} (GPIO{assignment.gpio})"
            )
        lines.append(f"// Tick interval: {self.tick_ms} ms")
        lines.append(f"// Spec hash (sha256/16): {digest}")
        return lines

    def _read_function(self, instance: InstanceTemplate) -> list[str]:
        if not instance.read_body and not instance.readings:
            return []
        lines = [f"void read_{instance.instance_id}() {{"]
        if instance.read_body:
            for line in instance.read_body.splitlines():
                lines.append(f"  {line}")
        for reading in instance.readings:
            if reading.cpp_type == "float":
                lines.append(
                    f"  if (!isnan({reading.expression})) {{ {reading.var} = "
                    f"{reading.expression}; {reading.valid_var} = true; }}"
                )
                lines.append(f"  else {{ {reading.valid_var} = false; }}")
            else:
                lines.append(f"  {reading.var} = {reading.expression}; {reading.valid_var} = true;")
        lines.append("}")
        return lines

    def _sampling_plan(self) -> tuple[list[str], list[str], list[str]]:
        """Per-instance sampling intervals.

        Returns ``(interval_constants, last_read_globals, setup_lines)`` for
        the instances that must not be read on every tick.  A part declares
        its own minimum interval when its protocol demands one (the DHT
        library answers a second read within 2000 ms with the previous
        packet), so those instances get their own schedule.
        """
        constants: list[str] = []
        globals_: list[str] = []
        setup: list[str] = []
        for instance in self.rendered.instances:
            if not (instance.read_body or instance.readings):
                continue
            if instance.min_interval_ms <= self.tick_ms:
                continue
            constants.append(
                f"const uint32_t SPARKPLAN_INTERVAL_{instance.instance_id}_MS = "
                f"{instance.min_interval_ms};"
            )
            globals_.append(f"static uint32_t last_read_{instance.instance_id}_ms = 0;")
            setup.append(
                f"last_read_{instance.instance_id}_ms = millis() - "
                f"SPARKPLAN_INTERVAL_{instance.instance_id}_MS;"
            )
        return constants, globals_, setup

    def _read_calls(self) -> list[str]:
        """The read calls of the loop, one schedule per instance."""
        lines: list[str] = []
        for instance in self.rendered.instances:
            if not (instance.read_body or instance.readings):
                continue
            call = f"read_{instance.instance_id}();"
            if instance.min_interval_ms <= self.tick_ms:
                lines.append(f"  {call}")
                continue
            lines.append(
                f"  if ((uint32_t)(now - last_read_{instance.instance_id}_ms) >= "
                f"SPARKPLAN_INTERVAL_{instance.instance_id}_MS) {{"
            )
            lines.append(f"    last_read_{instance.instance_id}_ms = now;")
            lines.append(f"    {call}")
            lines.append("  }")
        return lines

    def _i2c_bus(self, instance: InstanceTemplate) -> tuple[int, int] | None:
        """The (SDA, SCL) GPIO pair of an I2C instance, if it has one."""
        gpios: dict[str, int] = {}
        for binding in instance.pins:
            if binding.placeholder in {"SDA_PIN", "SCL_PIN"}:
                gpios[binding.placeholder] = binding.gpio
        if "SDA_PIN" in gpios and "SCL_PIN" in gpios:
            return (gpios["SDA_PIN"], gpios["SCL_PIN"])
        return None

    def build(self) -> BehaviorFirmware:
        """Render the whole sketch."""
        self._collect_readings()
        if self.errors_present():
            return BehaviorFirmware(
                sketch="",
                libraries=list(self.rendered.libraries),
                readings={},
                issues=self.issues,
                supported=False,
            )

        includes: list[str] = ["#include <Arduino.h>", "#include <math.h>", "#include <string.h>"]
        for instance in self.rendered.instances:
            for line in instance.includes:
                if line not in includes:
                    includes.append(line)

        pin_lines: list[str] = []
        for instance in self.rendered.instances:
            for binding in instance.pins:
                pin_lines.append(
                    f"const uint8_t {binding.var} = {binding.gpio};  // "
                    f"{instance.instance_id}.{binding.pin_name}"
                )

        global_lines: list[str] = []
        for instance in self.rendered.instances:
            global_lines.extend(instance.globals)

        state_lines: list[str] = []
        for reading in self._readings:
            if reading.cpp_type == "float":
                state_lines.append(f"float {reading.var} = NAN;")
            elif reading.cpp_type == "int":
                state_lines.append(f"int {reading.var} = 0;")
            else:
                state_lines.append(f"bool {reading.var} = false;")
            state_lines.append(f"bool {reading.valid_var} = false;")

        rule_state: list[str] = []
        rule_bodies: list[str] = []
        for behavior in self.spec.behaviors:
            for rule in behavior.rules:
                rule_state.append(self._rule_state(behavior, rule))
                rule_bodies.extend(self._rule_lines(behavior, rule))
        display_bodies: list[str] = []
        for behavior in self.spec.behaviors:
            display_bodies.extend(self._display_lines(behavior))

        if self.errors_present():
            # A rule or a display line that cannot be rendered makes the whole
            # build unsupported: a sketch that drops it would silently change
            # what the build does.
            return BehaviorFirmware(
                sketch="",
                libraries=list(self.rendered.libraries),
                readings={},
                issues=self.issues,
                supported=False,
            )

        interval_constants, interval_globals, interval_setup = self._sampling_plan()

        setup_lines = [*self.rendered.board_setup]
        setup_lines.extend(interval_setup)
        seen_buses: set[tuple[int, int]] = set()
        for instance in self.rendered.instances:
            if instance.i2c_begin:
                # The I2C bus is shared: it is brought up once, before the
                # first device on it is set up.
                bus = self._i2c_bus(instance)
                if bus is None or bus not in seen_buses:
                    if bus is not None:
                        seen_buses.add(bus)
                    setup_lines.append(instance.i2c_begin)
            setup_lines.extend(instance.setup)

        sketch_lines = [
            *self._header(),
            "",
            *includes,
            "",
            *pin_lines,
            "",
            *global_lines,
            "",
            f"const uint32_t SPARKPLAN_TICK_MS = {self.tick_ms};",
            *interval_constants,
            "",
            *state_lines,
            "",
            *rule_state,
            "",
            *interval_globals,
            "",
            *[
                line
                for instance in self.rendered.instances
                for line in self._read_function(instance)
            ],
            "",
            "void setup() {",
            *[f"  {line}" for line in setup_lines],
            "}",
            "",
            "void loop() {",
            "  static uint32_t last_tick_ms = 0;",
            "  const uint32_t now = millis();",
            "  if ((uint32_t)(now - last_tick_ms) < SPARKPLAN_TICK_MS) {",
            "    return;",
            "  }",
            "  last_tick_ms = now;",
            "",
            *self._read_calls(),
            "",
            *rule_bodies,
            *display_bodies,
            "}",
            "",
        ]
        return BehaviorFirmware(
            sketch="\n".join(sketch_lines),
            libraries=list(self.rendered.libraries),
            readings={reading.name: reading.var for reading in self._readings},
            issues=self.issues,
            supported=True,
        )

    def errors_present(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)


def generate_behavior_firmware(
    spec: SupportedBuild,
    catalog: Catalog,
    tick_ms: int = DEFAULT_TICK_MS,
) -> BehaviorFirmware:
    """Render a behaviour based build into a sketch.

    Returns a structured result: when the build cannot be rendered without
    inventing semantics, ``supported`` is false and ``issues`` explains why.
    """
    return _Builder(spec, catalog, tick_ms).build()


def unsupported_error(result: BehaviorFirmware) -> FirmwareUnsupportedError:
    """Turn an unsupported result into an exception carrying its issues."""
    message = "firmware generation is not supported for this build: " + "; ".join(
        issue.message for issue in result.errors
    )
    return FirmwareUnsupportedError(message, result.errors)
