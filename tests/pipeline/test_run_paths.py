from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

import pytest
from sparkplan.pipeline.run_models import RunResult, RunStatus
from sparkplan.pipeline.run_paths import (
    ARTIFACT_PATHS,
    RUNS_DIR_ENV,
    InvalidRunId,
    RunExistsError,
    RunPathError,
    RunPaths,
    UnsafeRunPath,
    resolve_runs_root,
    validate_run_id,
)


def claimed_paths(tmp_path: Path, run_id: str = "run_1") -> RunPaths:
    return RunPaths(run_id, tmp_path / "runs").claim()


@pytest.mark.parametrize("run_id", ["run", "RUN_1", "a-b_c", "a" * 64, "0"])
def test_valid_run_ids(run_id: str) -> None:
    assert validate_run_id(run_id) == run_id


@pytest.mark.parametrize(
    "run_id",
    ["", ".", "..", "a/b", "a\\b", "a b", "a.b", "a" * 65, "é", "a\x00b"],
)
def test_invalid_run_ids(run_id: str) -> None:
    with pytest.raises(InvalidRunId):
        validate_run_id(run_id)


def test_runs_root_uses_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "custom-runs"
    monkeypatch.setenv(RUNS_DIR_ENV, str(root))

    assert resolve_runs_root() == root.resolve()


def test_runs_root_rejects_a_file(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    root.write_text("not a directory")

    with pytest.raises(UnsafeRunPath, match="not a directory"):
        resolve_runs_root(root)


def test_claim_creates_the_run_directory_exclusively(tmp_path: Path) -> None:
    paths = RunPaths("run_1", tmp_path / "runs")

    assert paths.claimed is False

    paths.claim()

    assert paths.claimed is True
    assert paths.run_dir.is_dir()
    assert not paths.run_dir.is_symlink()


def test_claim_is_idempotent_for_the_same_instance(tmp_path: Path) -> None:
    paths = claimed_paths(tmp_path)

    assert paths.claim() is paths


def test_existing_run_directory_is_rejected_without_changes(tmp_path: Path) -> None:
    paths = claimed_paths(tmp_path)
    marker = paths.run_dir / "marker.txt"
    marker.write_text("original")

    with pytest.raises(RunExistsError):
        RunPaths("run_1", tmp_path / "runs").claim()

    assert marker.read_text() == "original"


def test_concurrent_claims_have_exactly_one_winner(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    root.mkdir()
    barrier = threading.Barrier(2)

    def claim_once() -> str:
        paths = RunPaths("run_1", root)
        barrier.wait()
        try:
            paths.claim()
        except RunExistsError:
            return "exists"
        return "claimed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim_once) for _ in range(2)]
        results = [future.result() for future in futures]

    assert sorted(results) == ["claimed", "exists"]


def test_planned_artifacts_stay_under_the_claimed_directory(tmp_path: Path) -> None:
    paths = claimed_paths(tmp_path)
    run_dir = paths.run_dir.resolve()

    for name, relative in ARTIFACT_PATHS.items():
        path = paths.artifact(name)
        assert path == paths.run_dir / relative
        assert path.resolve(strict=False).is_relative_to(run_dir)

    inventory = paths.artifact_inventory()
    assert inventory == {
        name: PurePosixPath(relative).as_posix() for name, relative in ARTIFACT_PATHS.items()
    }
    assert list(inventory) == sorted(inventory)


def test_unknown_artifact_is_rejected(tmp_path: Path) -> None:
    paths = claimed_paths(tmp_path)

    with pytest.raises(RunPathError, match="unknown artifact"):
        paths.artifact("../../outside")


def test_writes_require_a_claim(tmp_path: Path) -> None:
    paths = RunPaths("run_1", tmp_path / "runs")

    with pytest.raises(RunPathError, match="has not been claimed"):
        paths.write_json("spec", {"run_id": "run_1"})


def test_json_is_atomic_and_deterministically_ordered(tmp_path: Path) -> None:
    paths = claimed_paths(tmp_path)

    path = paths.write_json("spec", {"b": 2, "a": 1})

    assert path.read_text() == '{\n  "a": 1,\n  "b": 2\n}\n'
    assert list(json.loads(path.read_text())) == ["a", "b"]
    assert path.stat().st_mode & 0o777 == 0o644


def test_json_accepts_a_run_result(tmp_path: Path) -> None:
    paths = claimed_paths(tmp_path)
    result = RunResult(run_id="run_1", status=RunStatus.OK)

    path = paths.write_json("report", result)

    data = json.loads(path.read_text())
    assert data["run_id"] == "run_1"
    assert data["status"] == "ok"


def test_json_refuses_to_overwrite(tmp_path: Path) -> None:
    paths = claimed_paths(tmp_path)
    path = paths.write_json("report", {"status": "running"})
    original = path.read_text()

    with pytest.raises(FileExistsError):
        paths.write_json("report", {"status": "ok"})

    assert path.read_text() == original


def test_json_serialization_failure_leaves_no_file(tmp_path: Path) -> None:
    paths = claimed_paths(tmp_path)

    with pytest.raises(ValueError):
        paths.write_json("report", {"value": float("nan")})
    with pytest.raises(TypeError):
        paths.write_json("report", {"value": object()})

    assert not paths.artifact("report").exists()


def test_json_rejects_non_json_artifacts(tmp_path: Path) -> None:
    paths = claimed_paths(tmp_path)

    with pytest.raises(RunPathError, match="not JSON"):
        paths.write_json("guide", {"text": "hello"})


def test_text_and_binary_artifacts_refuse_overwrite(tmp_path: Path) -> None:
    paths = claimed_paths(tmp_path)

    guide = paths.write_text("guide", "# Guide\n")
    stl = paths.write_bytes("base_stl", b"solid base")

    assert guide.read_text() == "# Guide\n"
    assert stl.read_bytes() == b"solid base"
    with pytest.raises(FileExistsError):
        paths.write_text("guide", "# Changed\n")
    with pytest.raises(FileExistsError):
        paths.write_bytes("base_stl", b"solid changed")
    assert guide.read_text() == "# Guide\n"
    assert stl.read_bytes() == b"solid base"


def test_binary_writer_rejects_json_artifacts(tmp_path: Path) -> None:
    paths = claimed_paths(tmp_path)

    with pytest.raises(RunPathError, match="use write_json"):
        paths.write_bytes("spec", b"{}")


def test_escaping_file_symlink_is_rejected(tmp_path: Path) -> None:
    paths = claimed_paths(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("secret")
    os.symlink(outside, paths.artifact("spec"))

    with pytest.raises(UnsafeRunPath, match="symlink"):
        paths.write_json("spec", {"run_id": "run_1"})

    assert outside.read_text() == "secret"


def test_escaping_directory_symlink_is_rejected(tmp_path: Path) -> None:
    paths = claimed_paths(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, paths.run_dir / "sketch")

    with pytest.raises(UnsafeRunPath, match="symlink"):
        paths.write_text("sketch", "void setup() {}\n")

    assert not (outside / "sketch.ino").exists()


def test_escaping_run_directory_symlink_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, root / "run_1")

    with pytest.raises(UnsafeRunPath, match="symlink"):
        RunPaths("run_1", root)
