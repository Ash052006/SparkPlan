"""Action-graph checks: references, kinds, dataflow, coverage."""

from __future__ import annotations

from sparkplan.catalog import Catalog
from sparkplan.models.spec import BuildSpec, Issue

_KIND_FOR_API = {"read": "read", "write": "write"}


def _err(code: str, message: str, part_ids: list[str] | None = None, hint: str = "") -> Issue:
    return Issue(
        code=code, severity="error", message=message, part_ids=part_ids or [], fix_hint=hint
    )


def _warn(code: str, message: str, part_ids: list[str] | None = None, hint: str = "") -> Issue:
    return Issue(
        code=code, severity="warning", message=message, part_ids=part_ids or [], fix_hint=hint
    )


def validate_actions(spec: BuildSpec, catalog: Catalog) -> list[Issue]:
    issues: list[Issue] = []
    action_ids: set[str] = set()
    by_id = {}

    for action in spec.actions:
        if action.action_id in action_ids:
            issues.append(
                _err(
                    "DUPLICATE_ACTION_ID",
                    f"action_id {action.action_id!r} appears more than once.",
                    hint="Give every action a unique action_id.",
                )
            )
        action_ids.add(action.action_id)
        by_id[action.action_id] = action

        inst = spec.instance(action.instance_id)
        if inst is None:
            issues.append(
                _err(
                    "ACTION_UNKNOWN_INSTANCE",
                    f"action {action.action_id!r} references unknown instance "
                    f"{action.instance_id!r}.",
                    hint="Every action must target an instance listed in parts.",
                )
            )
            continue
        part = catalog.part_or_none(inst.part_id)
        if part is None:
            continue

        api = catalog.api_fn(inst.part_id, action.api)
        if api is None:
            valid = [f.name for f in part.firmware.api]
            issues.append(
                _err(
                    "ACTION_UNKNOWN_API",
                    f"part {part.part_id} has no api {action.api!r} (valid: {valid}).",
                    part_ids=[part.part_id],
                    hint="Use only api names declared by the catalog part.",
                )
            )
            continue

        expected = _KIND_FOR_API.get(api.kind, api.kind)
        if action.kind != expected:
            issues.append(
                _err(
                    "ACTION_KIND_MISMATCH",
                    f"action {action.action_id!r} is kind {action.kind!r} but api "
                    f"{api.name!r} is {api.kind!r}.",
                    part_ids=[part.part_id],
                    hint=f"Set kind to {expected!r}.",
                )
            )

    # Dataflow: consumes must point at a read; no cycles.
    for action in spec.actions:
        if action.consumes is None:
            continue
        source = by_id.get(action.consumes)
        if source is None:
            issues.append(
                _err(
                    "ACTION_CONSUMES_UNKNOWN",
                    f"action {action.action_id!r} consumes unknown action {action.consumes!r}.",
                    hint="consumes must reference an earlier read action.",
                )
            )
            continue
        if source.kind != "read":
            issues.append(
                _err(
                    "ACTION_CONSUMES_NOT_READ",
                    f"action {action.action_id!r} consumes {source.kind!r} action "
                    f"{source.action_id!r}.",
                    hint="Only read actions produce values to consume.",
                )
            )
    issues.extend(_detect_cycles(spec))

    # Coverage: every non-power instance should participate in at least one action.
    active = {a.instance_id for a in spec.actions}
    for inst in spec.parts:
        part = catalog.part_or_none(inst.part_id)
        if part is None:
            continue
        if part.category.value == "power":
            continue
        if not part.firmware.api:
            continue
        if inst.instance_id not in active:
            issues.append(
                _warn(
                    "UNUSED_PART",
                    f"{inst.instance_id} ({part.part_id}) has catalog firmware but no "
                    "actions use it.",
                    part_ids=[part.part_id],
                    hint="Add read/write actions for every functional part or remove it.",
                )
            )
    return issues


def _detect_cycles(spec: BuildSpec) -> list[Issue]:
    edges: dict[str, str] = {}
    for action in spec.actions:
        if action.consumes is not None:
            edges[action.action_id] = action.consumes
    for start in list(edges):
        seen: set[str] = set()
        node: str | None = start
        while node is not None and node in edges:
            if node in seen:
                return [
                    _err(
                        "ACTION_CYCLE",
                        f"action dataflow contains a cycle involving {node!r}.",
                        hint="Data flows from reads to writes; break the cycle.",
                    )
                ]
            seen.add(node)
            node = edges.get(node)
    return []
