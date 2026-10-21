"""Errors raised by the firmware stage.

The classes live here so the template engine, the behaviour generator and the
public ``sparkplan.firmware`` API can share them without importing each
other.
"""

from __future__ import annotations

from sparkplan.models.spec import Issue


class FirmwareError(RuntimeError):
    """Template assembly failed (e.g. unresolved placeholder)."""


class FirmwareUnsupportedError(FirmwareError):
    """The request cannot be rendered without inventing hardware semantics.

    The carrying issues are machine-readable so the caller can hand them back
    to the planner exactly like validator issues.
    """

    def __init__(self, message: str, issues: list[Issue] | None = None) -> None:
        super().__init__(message)
        self.issues: list[Issue] = list(issues or [])
