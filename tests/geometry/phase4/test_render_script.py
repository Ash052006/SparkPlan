"""Smoke test for scripts/render_geometry.py (headless matplotlib)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]


def _load_render_module() -> ModuleType:
    path = ROOT / "scripts" / "render_geometry.py"
    spec = importlib.util.spec_from_file_location("render_geometry", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_render_writes_png(tmp_path: Path) -> None:
    module = _load_render_module()
    out = tmp_path / "plan.png"
    rc = module.main(
        [
            "render_geometry.py",
            str(ROOT / "tests" / "fixtures" / "phase4" / "ultrasonic_alarm.json"),
            str(out),
        ]
    )
    assert rc == 0
    assert out.stat().st_size > 20_000


def test_missing_spec_is_usage_error(tmp_path: Path) -> None:
    module = _load_render_module()
    rc = module.main(["render_geometry.py", str(tmp_path / "nope.json")])
    assert rc == 2
