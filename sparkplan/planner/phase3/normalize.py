"""Deterministic normalization of common LLM BuildSpec aliases.

Language models sometimes put a part's ``instance_id`` in ``LayoutHint.part_id``
even though the hardware stages address layout hints by catalog ``part_id``.
They also sometimes write a connection board-first instead of peripheral-first,
while the validators and firmware stages expect the peripheral endpoint in
``from_*``. Both rewrites are semantics-preserving and unambiguous. Unknown or
ambiguous ids are left untouched so the validators still report them.
"""

from __future__ import annotations

from sparkplan.models.catalog import Catalog


def canonicalize_layout_aliases(data: object, catalog: Catalog) -> object:
    """Map layout hints that name an instance to that instance's catalog part."""
    if not isinstance(data, dict) or data.get("status") == "unsupported":
        return data

    parts = data.get("parts")
    layout = data.get("layout")
    if not isinstance(parts, list) or not isinstance(layout, list):
        return data

    instance_to_part: dict[str, str] = {}
    for part in parts:
        if not isinstance(part, dict):
            continue
        instance_id = part.get("instance_id")
        part_id = part.get("part_id")
        if isinstance(instance_id, str) and isinstance(part_id, str):
            instance_to_part[instance_id] = part_id

    known_part_ids = {part.id for part in catalog.parts}
    canonical_layout: list[object] = []
    seen_part_ids: set[str] = set()

    for hint in layout:
        if not isinstance(hint, dict):
            canonical_layout.append(hint)
            continue

        normalized = dict(hint)
        part_id = normalized.get("part_id")
        if isinstance(part_id, str) and part_id not in known_part_ids:
            mapped = instance_to_part.get(part_id)
            if mapped is not None:
                normalized["part_id"] = mapped
                part_id = mapped

        if isinstance(part_id, str):
            if part_id in seen_part_ids:
                continue
            seen_part_ids.add(part_id)

        canonical_layout.append(normalized)

    return {**data, "layout": canonical_layout}


def canonicalize_connection_directions(data: object, catalog: Catalog) -> object:
    """Rewrite board-first connections as peripheral-first."""
    if not isinstance(data, dict) or data.get("status") == "unsupported":
        return data

    parts = data.get("parts")
    connections = data.get("connections")
    if not isinstance(parts, list) or not isinstance(connections, list):
        return data

    board_instances: set[str] = set()
    for part in parts:
        if not isinstance(part, dict):
            continue
        instance_id = part.get("instance_id")
        part_id = part.get("part_id")
        if not isinstance(instance_id, str) or not isinstance(part_id, str):
            continue
        catalog_part = catalog.get_or_none(part_id)
        if catalog_part is not None and catalog_part.category.value == "board":
            board_instances.add(instance_id)

    canonical: list[object] = []
    for connection in connections:
        if not isinstance(connection, dict):
            canonical.append(connection)
            continue

        from_instance = connection.get("from_instance")
        to_instance = connection.get("to_instance")
        from_board = from_instance in board_instances
        to_board = to_instance in board_instances
        if from_board and not to_board:
            canonical.append(
                {
                    **connection,
                    "from_instance": to_instance,
                    "from_pin": connection.get("to_pin"),
                    "to_instance": from_instance,
                    "to_pin": connection.get("from_pin"),
                }
            )
        else:
            canonical.append(connection)

    return {**data, "connections": canonical}
