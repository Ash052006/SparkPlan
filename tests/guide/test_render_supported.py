from __future__ import annotations

from sparkplan.guide import RunVerification, render_supported_guide
from sparkplan.models.catalog import get_catalog
from sparkplan.models.spec import Issue
from tests.planner.phase3.conftest import as_supported, valid_spec


def _guide(verification: RunVerification | None = None) -> str:
    return render_supported_guide(
        as_supported(valid_spec()),
        get_catalog(),
        None,
        None,
        [
            Issue(
                code="WIRE_NOTE",
                severity="warning",
                message="Check the shared ground.",
                part_ids=[],
                fix_hint="Continuity-test all ground connections.",
            )
        ],
        verification=verification,
    )


def test_supported_guide_is_self_contained_and_deterministic() -> None:
    first = _guide()
    second = _guide()

    assert first == second
    assert "| instance | part | quantity | role | supply | typ/peak mA | mount |" in first
    assert "| soil | Capacitive soil moisture sensor v2 | 1 | sensor |" in first
    assert "| soil.VCC | board.3V3 | rail (3V3) | red |" in first
    assert "| screen.SDA | board.D21 | sda | blue |" in first
    assert "```mermaid\ngraph LR" in first
    assert 'N0["board<br/>ESP32 DevKit 30-pin (ESP32-WROOM-32)"]' in first
    assert "## 3. Power notes" in first
    assert "## 6. Print settings" in first
    assert "- Material: PLA or PETG" in first
    assert "- Layer height: 0.2 mm" in first
    assert "## 8. Assembly" in first
    assert "## 9. Pre-flight test checklist" in first
    assert "Confirm the **monitor** behaviour works" in first
    assert "WIRE_NOTE" in first
    assert "Continuity-test all ground connections." in first
    assert "## 10. Verification" in first
    assert first.endswith("Not physically built.")


def test_verification_values_are_explicit_and_simulated_is_skipped() -> None:
    guide = _guide(
        RunVerification(
            validated=True,
            compiled=False,
            sliced="skipped",
            simulated="skipped",
        )
    )

    assert "| Validated | true |" in guide
    assert "| Compiled | false |" in guide
    assert "| Sliced | skipped |" in guide
    assert "| Simulated | skipped |" in guide


def test_verification_defaults_to_skipped_not_passed() -> None:
    guide = _guide()

    assert "| Validated | skipped |" in guide
    assert "| Compiled | skipped |" in guide
    assert "| Sliced | skipped |" in guide
    assert "| Simulated | skipped |" in guide
    assert "| Validated | true |" not in guide
