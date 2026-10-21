"""Firmware generation context - normalize both spec shapes.

Adapts between sparkplan.models.spec.BuildSpec (legacy, has actions)
and sparkplan.models.buildspec.SupportedBuild (Phase 2/3/4, has behaviors)
without dropping data or guessing semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sparkplan.models.buildspec import Behavior, SupportedBuild
from sparkplan.models.spec import Action, BuildSpec, Connection, PartInstance


@dataclass(frozen=True)
class FirmwareGenerationContext:
    """Normalized context for firmware generation."""

    idea: str
    instances: dict[str, PartInstance]
    connections: list[Connection]
    actions: list[Action]
    behaviors: list[Behavior]
    layout_hints: list[Any]
    source_kind: Literal["legacy_spec", "buildspec"]
    canonical_spec_dict: dict[str, Any]


def _connection_from_buildspec(c: Any) -> Connection:
    """Map buildspec Connection to legacy Connection shape."""
    # buildspec.Connection has from_instance/from_pin/to_instance/to_pin
    # legacy Connection has instance_id/peripheral_pin/controller_pin
    # We need to infer which side is peripheral vs controller.
    # Heuristic: if from_instance is board/controller -> peripheral is to_instance.pin?
    # Or just preserve - firmware uses instance_id + peripheral_pin
    # Try to be reasonable
    if hasattr(c, "instance_id"):
        return Connection(
            instance_id=c.instance_id,
            peripheral_pin=c.peripheral_pin,
            controller_pin=c.controller_pin,
        )
    # buildspec shape
    return Connection(
        instance_id=c.from_instance,
        peripheral_pin=c.from_pin,
        controller_pin=c.to_pin,
    )


def from_legacy(spec: BuildSpec) -> FirmwareGenerationContext:
    return FirmwareGenerationContext(
        idea=spec.idea,
        instances={p.instance_id: p for p in spec.parts},
        connections=list(spec.connections),
        actions=list(spec.actions),
        behaviors=[],
        layout_hints=[],
        source_kind="legacy_spec",
        canonical_spec_dict=spec.model_dump(mode="json"),
    )


def from_buildspec(spec: SupportedBuild) -> FirmwareGenerationContext:
    """Adapt SupportedBuild. Does NOT synthesize actions - returns empty actions."""
    # Map buildspec connections to legacy shape
    connections = []
    for c in spec.connections:
        try:
            connections.append(_connection_from_buildspec(c))
        except Exception:
            continue  # don't guess

    instances = {}
    for p in spec.parts:
        instances[p.instance_id] = PartInstance(instance_id=p.instance_id, part_id=p.part_id)

    return FirmwareGenerationContext(
        idea=spec.summary or spec.title,
        instances=instances,
        connections=connections,
        actions=[],
        behaviors=list(spec.behaviors),
        layout_hints=list(spec.layout),
        source_kind="buildspec",
        canonical_spec_dict=spec.model_dump(mode="json"),
    )


def make_context(spec: BuildSpec | SupportedBuild) -> FirmwareGenerationContext:
    if isinstance(spec, SupportedBuild):
        return from_buildspec(spec)
    if isinstance(spec, BuildSpec):
        return from_legacy(spec)
    # try duck typing
    if getattr(spec, "behaviors", None) is not None and getattr(spec, "actions", None) is None:
        return from_buildspec(spec)
    if getattr(spec, "actions", None) is not None:
        return from_legacy(spec)
    raise TypeError(f"unsupported spec type for firmware: {type(spec)}")
