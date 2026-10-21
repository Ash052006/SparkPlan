"""Deterministic template engine for the Phase 2+ catalog.

The engine turns a validated :class:`~sparkplan.models.buildspec.SupportedBuild`
plus the per-part firmware templates in ``catalog/parts.json`` into C++
fragments.  The rules that keep it honest:

* GPIOs come only from validated connections in the build spec.
* A template placeholder that cannot be resolved is a hard error, never a
  guess.
* Every instance gets its own pin constants and its own copy of the part's
  C++ object, so two instances of the same part cannot collide.
* Nothing is dropped silently: unresolved pins, unknown parts and connections
  the template cannot honour are all reported as issues.
* The same spec always renders the same bytes.

Units: millimetres, volts, milliamps.  GPIO numbers are plain integers.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from sparkplan.models.buildspec import SupportedBuild
from sparkplan.models.catalog import Catalog, Category, Interface, Part
from sparkplan.models.spec import Issue

# A template pin placeholder such as ``AOUT_PIN``.
_PLACEHOLDER_RE = re.compile(r"\b[A-Z][A-Z0-9_]*_PIN\b")
# C++ identifiers we are willing to emit.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Fallback unit -> C++ type table, used when the catalog does not declare a
# type for a reading.  Documented in docs/PROGRESS.md.
_BOOL_UNITS = {"bool", "boolean"}
_INT_UNITS = {"adc_counts"}


@dataclass(frozen=True)
class TemplateIssue:
    """A machine-readable problem found while rendering templates."""

    code: str
    message: str
    part_ids: list[str] = field(default_factory=list)
    fix_hint: str = ""
    severity: str = "error"

    def to_issue(self) -> Issue:
        return Issue(
            code=self.code,
            severity=self.severity,
            message=self.message,
            part_ids=list(self.part_ids),
            fix_hint=self.fix_hint,
        )


@dataclass(frozen=True)
class PinBinding:
    """One template pin placeholder bound to a validated GPIO."""

    instance_id: str
    placeholder: str
    pin_name: str
    gpio: int
    var: str


@dataclass(frozen=True)
class ReadingPlan:
    """One generated reading variable, scoped to its instance."""

    instance_id: str
    reading_name: str
    unit: str
    cpp_type: str
    var: str
    valid_var: str
    expression: str


@dataclass(frozen=True)
class ActionTemplate:
    """One callable action template of a part instance."""

    instance_id: str
    name: str
    snippet: str
    params: tuple[str, ...]


@dataclass(frozen=True)
class InstanceTemplate:
    """The rendered firmware fragments of one part instance."""

    instance_id: str
    part_id: str
    object_var: str
    pins: tuple[PinBinding, ...]
    includes: tuple[str, ...]
    globals: tuple[str, ...]
    setup: tuple[str, ...]
    read_body: str
    readings: tuple[ReadingPlan, ...]
    actions: tuple[ActionTemplate, ...]
    i2c_begin: str = ""
    min_interval_ms: int = 0


@dataclass(frozen=True)
class GpioAssignment:
    """One validated connection from a peripheral pin to a board GPIO."""

    instance_id: str
    pin_name: str
    board_pin: str
    gpio: int


@dataclass(frozen=True)
class RenderedTemplate:
    """Everything the sketch writer needs, plus the issues found on the way."""

    instances: tuple[InstanceTemplate, ...]
    libraries: tuple[str, ...]
    issues: tuple[TemplateIssue, ...]
    board_setup: tuple[str, ...] = ()
    gpio_assignments: tuple[GpioAssignment, ...] = ()

    @property
    def errors(self) -> list[TemplateIssue]:
        return [i for i in self.issues if i.severity == "error"]


def board_pin_to_gpio(pin: str) -> int | None:
    """Map a board pin label to its GPIO number.

    ``D4`` and ``GPIO4`` both mean GPIO 4, which is the convention the Phase 2
    pin validator uses.  Labels without a number (``3V3``, ``GND``, ``VP``)
    carry no GPIO.
    """
    cleaned = pin.strip().upper()
    if cleaned.startswith("GPIO"):
        cleaned = cleaned[4:]
    elif cleaned.startswith("D") and cleaned[1:].isdigit():
        cleaned = cleaned[1:]
    if cleaned.isdigit():
        return int(cleaned)
    return None


def cpp_type_for(unit: str) -> str:
    """Fallback C++ type for a reading unit.

    ``bool``/``boolean`` -> ``bool``, ``adc_counts`` -> ``int``, everything
    else -> ``float``.
    """
    normalised = (unit or "").strip().lower()
    if normalised in _BOOL_UNITS:
        return "bool"
    if normalised in _INT_UNITS:
        return "int"
    return "float"


def canonical_payload(spec: SupportedBuild | object) -> dict[str, object]:
    """Order-independent canonical form of a build spec.

    ``parts``, ``connections`` and ``layout`` are sorted because their order
    carries no meaning.  ``actions`` and ``behaviors`` keep their order
    because that order decides what the firmware does.
    """
    payload = spec.model_dump(mode="json")  # type: ignore[attr-defined]
    for key in ("parts", "connections", "layout"):
        value = payload.get(key)
        if isinstance(value, list):
            payload[key] = sorted(value, key=lambda item: json.dumps(item, sort_keys=True))
    behaviours = payload.get("behaviors")
    if isinstance(behaviours, list):
        for behaviour in behaviours:
            for key in ("readings", "display_lines"):
                value = behaviour.get(key)
                if isinstance(value, list):
                    behaviour[key] = sorted(
                        value, key=lambda item: json.dumps(item, sort_keys=True)
                    )
    return payload


def canonical_hash(payload: dict[str, object]) -> str:
    """Stable short hash of a canonical payload."""
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class TemplateEngine:
    """Render the firmware fragments of every part instance in a build."""

    def __init__(self, spec: SupportedBuild, catalog: Catalog) -> None:
        self.spec = spec
        self.catalog = catalog
        self.issues: list[TemplateIssue] = []
        self._board_instances: set[str] = set()
        # (instance_id, pin name) -> board pin label
        self._board_pin: dict[tuple[str, str], str] = {}
        # (instance_id, pin name) -> gpio number
        self._gpio: dict[tuple[str, str], int] = {}

    # -- issues ---------------------------------------------------------
    def _issue(
        self,
        code: str,
        message: str,
        part_ids: list[str] | None = None,
        fix_hint: str = "",
        severity: str = "error",
    ) -> None:
        self.issues.append(
            TemplateIssue(
                code=code,
                message=message,
                part_ids=list(part_ids or []),
                fix_hint=fix_hint,
                severity=severity,
            )
        )

    # -- indexing -------------------------------------------------------
    def _index_boards(self) -> None:
        for ref in self.spec.parts:
            part = self.catalog.get_or_none(ref.part_id)
            if part is not None and part.category is Category.BOARD:
                self._board_instances.add(ref.instance_id)

    def _index_connections(self) -> None:
        """Map peripheral pins to the board pin they are wired to.

        The build spec lists a connection as ``from`` (peripheral) to ``to``
        (board).  A reversed connection is handled by looking at which side
        is the board instead of by guessing from the order.
        """
        for conn in self.spec.connections:
            from_part = self.catalog.get_or_none(self._part_id(conn.from_instance))
            to_part = self.catalog.get_or_none(self._part_id(conn.to_instance))
            if from_part is None or to_part is None:
                continue
            if from_part.category is Category.BOARD and to_part.category is not Category.BOARD:
                peripheral, peripheral_pin = conn.to_instance, conn.to_pin
                board_pin = conn.from_pin
            elif to_part.category is Category.BOARD and from_part.category is not Category.BOARD:
                peripheral, peripheral_pin = conn.from_instance, conn.from_pin
                board_pin = conn.to_pin
            else:
                # Board-to-board or peripheral-to-peripheral: no GPIO binding.
                continue
            if conn.signal_role in {"rail", "gnd", "passive"}:
                continue
            self._board_pin[(peripheral, peripheral_pin)] = board_pin
            gpio = board_pin_to_gpio(board_pin)
            if gpio is None:
                self._issue(
                    "FIRMWARE_UNRESOLVED_PLACEHOLDER",
                    f"board pin {board_pin!r} for {peripheral}.{peripheral_pin} is not a "
                    "numbered GPIO",
                    [self._part_id(peripheral)],
                    "Target a numbered board pin such as 'D4'.",
                )
                continue
            self._gpio[(peripheral, peripheral_pin)] = gpio

    def _part_id(self, instance_id: str) -> str:
        ref = self.spec.instance(instance_id)
        return ref.part_id if ref is not None else ""

    def _gpio_for(self, instance_id: str, pin_name: str) -> int | None:
        return self._gpio.get((instance_id, pin_name))

    # -- rendering ------------------------------------------------------
    def render(self) -> RenderedTemplate:
        """Render every non-board instance of the build."""
        self._index_boards()
        self._index_connections()
        instances: list[InstanceTemplate] = []
        libraries: list[str] = []
        board_setup: list[str] = []
        for ref in self.spec.parts:
            part = self.catalog.get_or_none(ref.part_id)
            if part is None:
                self._issue(
                    "FIRMWARE_UNKNOWN_PART",
                    f"instance {ref.instance_id!r} uses unknown part {ref.part_id!r}",
                    [ref.part_id],
                    "Use a catalog part id.",
                )
                continue
            if ref.instance_id in self._board_instances:
                for line in part.firmware.setup_lines:
                    if line not in board_setup:
                        board_setup.append(line)
                continue
            rendered = self._render_instance(ref.instance_id, part)
            if rendered is None:
                continue
            instances.append(rendered)
            for library in part.firmware.library_deps:
                if library not in libraries:
                    libraries.append(library)
        assignments = tuple(
            GpioAssignment(
                instance_id=instance_id,
                pin_name=pin_name,
                board_pin=self._board_pin[(instance_id, pin_name)],
                gpio=gpio,
            )
            for (instance_id, pin_name), gpio in sorted(self._gpio.items())
        )
        return RenderedTemplate(
            instances=tuple(instances),
            libraries=tuple(libraries),
            issues=tuple(self.issues),
            board_setup=tuple(board_setup),
            gpio_assignments=assignments,
        )

    def _scoped_object(self, instance_id: str, object_name: str) -> str:
        """Instance-scoped name of the part's C++ object."""
        if not object_name:
            return ""
        if object_name == instance_id or object_name.startswith(f"{instance_id}_"):
            return object_name
        return f"{instance_id}_{object_name}"

    def _pin_name_for(self, part: Part, placeholder: str) -> str | None:
        """Catalog pin name a template placeholder refers to."""
        mapped = part.firmware.pin_map.get(placeholder)
        if mapped:
            return mapped
        for pin in part.pins:
            if f"{pin.name.upper()}_PIN" == placeholder:
                return pin.name
        return None

    def _render_instance(self, instance_id: str, part: Part) -> InstanceTemplate | None:
        firmware = part.firmware
        object_var = self._scoped_object(instance_id, firmware.object_name)
        template_text = "\n".join(
            [
                *firmware.globals,
                *firmware.setup_lines,
                firmware.read_snippet,
                *(action.snippet for action in firmware.actions),
            ]
        )
        placeholders = sorted(set(_PLACEHOLDER_RE.findall(template_text)))

        bindings: list[PinBinding] = []
        substitutions: dict[str, str] = {}
        for placeholder in placeholders:
            pin_name = self._pin_name_for(part, placeholder)
            if pin_name is None:
                self._issue(
                    "FIRMWARE_UNRESOLVED_PLACEHOLDER",
                    f"part {part.id!r} template placeholder {placeholder!r} does not name a "
                    "catalog pin",
                    [part.id],
                    f"Add a firmware.pin_map entry for {placeholder!r} or rename the pin.",
                )
                continue
            gpio = self._gpio_for(instance_id, pin_name)
            if gpio is None:
                self._issue(
                    "FIRMWARE_UNRESOLVED_PLACEHOLDER",
                    f"instance {instance_id!r} has no validated connection for pin "
                    f"{pin_name!r} ({placeholder})",
                    [part.id],
                    "Connect the pin to a board GPIO in the build spec.",
                )
                continue
            var = f"{instance_id}_{placeholder}"
            bindings.append(
                PinBinding(
                    instance_id=instance_id,
                    placeholder=placeholder,
                    pin_name=pin_name,
                    gpio=gpio,
                    var=var,
                )
            )
            substitutions[placeholder] = var

        i2c_begin = ""
        if part.interface is Interface.I2C:
            i2c_begin = self._render_i2c_begin(instance_id, part, bindings, substitutions)

        includes = tuple(
            self._substitute(line, substitutions, firmware.object_name, object_var)
            for line in firmware.includes
        )
        globals_ = tuple(
            self._substitute(line, substitutions, firmware.object_name, object_var)
            for line in firmware.globals
        )
        setup = tuple(
            self._substitute(line, substitutions, firmware.object_name, object_var)
            for line in firmware.setup_lines
        )
        read_body = self._substitute(
            firmware.read_snippet, substitutions, firmware.object_name, object_var
        )
        actions = tuple(
            ActionTemplate(
                instance_id=instance_id,
                name=action.name,
                snippet=self._substitute(
                    action.snippet, substitutions, firmware.object_name, object_var
                ),
                params=tuple(action.params),
            )
            for action in firmware.actions
        )
        readings = self._readings_for(instance_id, part)
        if readings is None:
            return None
        return InstanceTemplate(
            instance_id=instance_id,
            part_id=part.id,
            object_var=object_var,
            pins=tuple(bindings),
            includes=includes,
            globals=globals_,
            setup=setup,
            read_body=read_body,
            readings=readings,
            actions=actions,
            i2c_begin=i2c_begin,
            min_interval_ms=firmware.min_interval_ms,
        )

    def _render_i2c_begin(
        self,
        instance_id: str,
        part: Part,
        bindings: list[PinBinding],
        substitutions: dict[str, str],
    ) -> str:
        """Emit ``Wire.begin(sda, scl)`` from the validated I2C connections."""
        pins: list[str] = []
        for pin_name in ("SDA", "SCL"):
            placeholder = f"{pin_name}_PIN"
            if placeholder in substitutions:
                pins.append(substitutions[placeholder])
                continue
            gpio = self._gpio_for(instance_id, pin_name)
            if gpio is None:
                self._issue(
                    "FIRMWARE_UNRESOLVED_PLACEHOLDER",
                    f"I2C part {part.id!r} instance {instance_id!r} has no validated "
                    f"{pin_name} connection",
                    [part.id],
                    f"Connect {pin_name} to a board GPIO in the build spec.",
                )
                continue
            var = f"{instance_id}_{placeholder}"
            bindings.append(
                PinBinding(
                    instance_id=instance_id,
                    placeholder=placeholder,
                    pin_name=pin_name,
                    gpio=gpio,
                    var=var,
                )
            )
            substitutions[placeholder] = var
            pins.append(var)
        if len(pins) != 2:
            return ""
        return f"Wire.begin({pins[0]}, {pins[1]});"

    def _readings_for(self, instance_id: str, part: Part) -> tuple[ReadingPlan, ...] | None:
        readings: list[ReadingPlan] = []
        seen: set[str] = set()
        failed = False
        for reading in part.firmware.readings:
            var = f"{instance_id}_{reading.name}"
            if not _IDENT_RE.match(var):
                self._issue(
                    "FIRMWARE_INVALID_IDENTIFIER",
                    f"reading {instance_id!r}/{reading.name!r} does not form a valid C++ "
                    "identifier",
                    [part.id],
                    "Use lower-case names with digits and underscores.",
                )
                continue
            if var in seen:
                self._issue(
                    "FIRMWARE_DUPLICATE_READING",
                    f"reading variable {var!r} is declared twice",
                    [part.id],
                    "Rename one of the readings.",
                )
                continue
            seen.add(var)
            if not reading.expression:
                self._issue(
                    "FIRMWARE_MISSING_EXPRESSION",
                    f"reading {var!r} has no expression binding it to the read snippet",
                    [part.id],
                    "Add a firmware.readings[].expression for this reading.",
                )
                failed = True
                continue
            readings.append(
                ReadingPlan(
                    instance_id=instance_id,
                    reading_name=reading.name,
                    unit=reading.unit,
                    cpp_type=reading.type or cpp_type_for(reading.unit),
                    var=var,
                    valid_var=f"{var}_valid",
                    expression=reading.expression,
                )
            )
        if failed:
            return None
        return tuple(readings)

    def _substitute(
        self,
        text: str,
        substitutions: dict[str, str],
        object_name: str,
        object_var: str,
    ) -> str:
        """Replace pin placeholders and scope the part's object name.

        A name that follows a dot is a member of the object, not the object
        itself, so ``display.display()`` keeps its method name.
        """
        for placeholder, value in substitutions.items():
            text = re.sub(rf"(?<![.\w]){re.escape(placeholder)}\b", value, text)
        if object_name and object_var and object_name != object_var:
            text = re.sub(rf"(?<![.\w]){re.escape(object_name)}\b", object_var, text)
        return text


def issues_to_spec_issues(issues: list[TemplateIssue]) -> list[Issue]:
    """Convert template issues into validator-style issues."""
    return [issue.to_issue() for issue in issues]
