"""Requirement 8: the prompt is built from the live catalog/board model.

No GPIO numbers are hardcoded; the digest reflects the actual board model and
part facts and states that current figures are approximate/unverified.

These tests pin the *compact* digest shape (the Phase 3 prompt was made small
to fit a small provider token budget) while proving every required fact is
still present: part selection, connections, pin capabilities, power and I2C.
"""

from __future__ import annotations

import json

from sparkplan.planner.phase3.prompt import build_system_prompt, catalog_digest


def test_digest_uses_actual_board_default_i2c_pins(catalog):
    board = catalog.board()
    assert board.board is not None
    digest = json.loads(catalog_digest(catalog))
    assert digest["board"]["i2c_default"]["sda"] == board.board.default_i2c_sda_gpio
    assert digest["board"]["i2c_default"]["scl"] == board.board.default_i2c_scl_gpio


def test_digest_reflects_board_gpio_rules(catalog):
    board = catalog.board()
    assert board.board is not None
    digest = json.loads(catalog_digest(catalog))
    gpio = digest["board"]["gpio"]
    model = board.board
    assert set(gpio["forbidden"]) == {g.gpio for g in model.gpio if g.forbidden}
    assert set(gpio["input_only"]) == {g.gpio for g in model.gpio if g.input_only}
    assert set(gpio["adc"]) == {g.gpio for g in model.gpio if g.caps.adc}
    assert set(gpio["pwm"]) == {g.gpio for g in model.gpio if g.caps.pwm}
    assert set(gpio["i2c"]) == {g.gpio for g in model.gpio if g.caps.i2c}
    assert set(gpio["strapping_warn"]) == {g.gpio for g in model.gpio if g.strapping}
    # Every board pin is accounted for exactly once across the categories.
    assert digest["board"]["gpio_total"] == len(model.gpio)


def test_digest_includes_part_facts(catalog):
    digest = json.loads(catalog_digest(catalog))
    ids = {p["id"] for p in digest["parts"]}
    assert catalog.board().id not in ids  # board is not a "part"
    any_part = digest["parts"][0]
    for key in ("id", "caps", "pins", "v", "i_ma", "iface"):
        assert key in any_part


def test_digest_includes_i2c_address_for_i2c_parts(catalog):
    digest = json.loads(catalog_digest(catalog))
    oled = next(p for p in digest["parts"] if p["iface"] == "i2c")
    assert oled["i2c"] is not None


def test_pins_encode_role_for_wiring(catalog):
    digest = json.loads(catalog_digest(catalog))
    oled = next(p for p in digest["parts"] if p["id"].startswith("ssd1306"))
    assert any(pin.startswith("SDA:") for pin in oled["pins"])
    assert any(pin.startswith("SCL:") for pin in oled["pins"])


def test_system_prompt_states_hard_rules_and_schema(catalog):
    system = build_system_prompt(catalog)
    assert "HARD RULES" in system
    assert "unsupported" in system
    assert "missing_capabilities" in system
    assert "closest_supported_alternative" in system
    assert "SCHEMA" in system
    assert "APPROXIMATE" in system


def test_prompt_marks_currents_approximate_not_safe(catalog):
    digest = json.loads(catalog_digest(catalog))
    # Rails carry the approximate flag from the catalog, so the LLM can see the
    # figures are not verified facts.
    assert any(r["approx"] for r in digest["board"]["rails"])
    assert "APPROXIMATE" in build_system_prompt(catalog)


def test_digest_omits_default_base_mount(catalog):
    # Compactness: parts mounted on the base (the default) do not repeat it.
    digest = json.loads(catalog_digest(catalog))
    for p in digest["parts"]:
        if "mount" in p:
            assert p["mount"] != "base"


def test_digest_omits_default_logic_level(catalog):
    digest = json.loads(catalog_digest(catalog))
    for p in digest["parts"]:
        if "logic_v" in p:
            assert p["logic_v"] != 3.3


def test_digest_omits_prose_description(catalog):
    # Token saving: prose descriptions duplicate the capability list and are not
    # read by any validator, so they are not sent.
    digest = json.loads(catalog_digest(catalog))
    for p in digest["parts"]:
        assert "desc" not in p
        assert "description" not in p
        assert p["caps"]  # the actual selection signal is retained


def test_prompt_states_concrete_power_precheck(catalog):
    system = build_system_prompt(catalog)
    # The model is told to sum per-rail draw, use peak current for surging parts,
    # and to refuse rather than emit an over-budget build.
    assert "PEAK i_ma" in system
    assert "max_ma" in system
    assert "unsupported" in system


def test_prompt_requires_every_pin_connected(catalog):
    system = build_system_prompt(catalog)
    assert "EVERY pin" in system


def test_system_prompt_states_scope_constraint(catalog):
    # Initial generation must be told to build only what the idea asks for and
    # not to add optional peripherals.
    system = build_system_prompt(catalog)
    assert "SCOPE" in system
    assert "Do NOT add optional components" in system
    assert "no buzzer, LED, display, sensor, motor or other peripheral" in system
    assert "requests it or it is genuinely required" in system


def test_scope_rule_does_not_relax_other_rules(catalog):
    # The scope rule must explicitly preserve wiring, layout, power and units,
    # and forbid dropping required parts/safety measures.
    system = build_system_prompt(catalog)
    flat = " ".join(system.split())
    assert "does NOT relax any other rule" in flat
    assert "connection, layout, power and unit rules" in flat
    assert "NOT drop a required part or a safety measure" in flat


def test_unsupported_outcome_retained_under_scope_rule(catalog):
    # A required capability the catalog lacks must still use the unsupported
    # outcome, not an invented component.
    system = build_system_prompt(catalog)
    flat = " ".join(system.split())
    assert "do NOT invent a component to cover it" in flat
    assert '"status":"unsupported"' in flat
