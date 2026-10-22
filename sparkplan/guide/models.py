"""Typed verification results shown in the deterministic build guide.

A check is ``True`` only when the evidence for it actually ran and passed.
``False`` means it was attempted and failed. ``"skipped"`` means it was not
attempted or could not be verified, including when an external tool is
missing. It is never used as a synonym for success.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

VerificationValue = bool | Literal["skipped"]


class RunVerification(BaseModel):
    """The four honest verification outcomes reported by a canonical run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    validated: VerificationValue = "skipped"
    compiled: VerificationValue = "skipped"
    sliced: VerificationValue = "skipped"
    simulated: VerificationValue = "skipped"

    def as_dict(self) -> dict[str, bool | str]:
        return {
            "validated": self.validated,
            "compiled": self.compiled,
            "sliced": self.sliced,
            "simulated": self.simulated,
        }
