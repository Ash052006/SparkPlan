from __future__ import annotations

from tests.validators.phase2.conftest import error_codes, run


def test_hc_sr04_echo_direct_fails():
    report = run("v5/echo_direct.json")
    assert "LOGIC_LEVEL_HIGH" in error_codes(report)


def test_hc_sr04_echo_direct_fix_hint_names_divider():
    report = run("v5/echo_direct.json")
    high = [i for i in report.errors if i.code == "LOGIC_LEVEL_HIGH"]
    assert high
    assert any("voltage_divider_5v_3v3" in i.fix_hint for i in high)


def test_echo_into_output_pin_fails():
    assert "LOGIC_LEVEL_HIGH" in error_codes(run("v5/echo_direct_output_pin.json"))


def test_echo_through_divider_passes():
    report = run("v5/valid_divider.json")
    assert report.passed, [i.code for i in report.errors]
    assert "LOGIC_LEVEL_HIGH" not in error_codes(report)
