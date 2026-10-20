"""In-process CLI tests so coverage sees sparkplan/validators/phase2/__main__.py."""

from __future__ import annotations

import json

from sparkplan.validators.phase2.__main__ import main
from tests.validators.phase2.conftest import FIXTURES


def test_main_passing(capsys):
    rc = main([str(FIXTURES / "v1" / "valid.json")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Validation: PASS" in out
    assert "Timing (ms)" in out


def test_main_failing(capsys):
    rc = main([str(FIXTURES / "v1" / "unknown_part.json")])
    out = capsys.readouterr().out
    assert rc == 1
    assert "UNKNOWN_PART" in out


def test_main_usage(capsys):
    rc = main([])
    err = capsys.readouterr().err
    assert rc == 2
    assert "usage:" in err


def test_main_bad_json(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    rc = main([str(bad)])
    assert rc == 2
    assert "could not read" in capsys.readouterr().err


def test_main_unsupported_spec(tmp_path, capsys):
    unsupported = {
        "status": "unsupported",
        "title": "Laser",
        "reasons": ["no laser in catalog"],
    }
    path = tmp_path / "unsupported.json"
    path.write_text(json.dumps(unsupported))
    rc = main([str(path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "unsupported" in out
