"""Phase 2 deterministic validators.

One module per validator, each a pure function
``(spec, catalog, board) -> list[Issue]``. ``run_all`` runs them all and returns
a :class:`ValidationReport`.
"""

from sparkplan.validators.phase2.behaviors import validate_behaviors
from sparkplan.validators.phase2.completeness import validate_completeness
from sparkplan.validators.phase2.conflicts import validate_conflicts
from sparkplan.validators.phase2.i2c import validate_i2c
from sparkplan.validators.phase2.layout import validate_layout
from sparkplan.validators.phase2.logic_levels import validate_logic_levels
from sparkplan.validators.phase2.membership import validate_membership
from sparkplan.validators.phase2.pins_check import validate_pins
from sparkplan.validators.phase2.power_check import validate_power
from sparkplan.validators.phase2.report import (
    ValidationReport,
    error_issues,
    has_errors,
    issue,
    run_all,
    sort_issues,
)

__all__ = [
    "ValidationReport",
    "error_issues",
    "has_errors",
    "issue",
    "run_all",
    "sort_issues",
    "validate_behaviors",
    "validate_completeness",
    "validate_conflicts",
    "validate_i2c",
    "validate_layout",
    "validate_logic_levels",
    "validate_membership",
    "validate_pins",
    "validate_power",
]
