from __future__ import annotations

import importlib
import io
import json
import os
import zipfile
from pathlib import Path
from typing import Any

import pytest
from api.app import create_app
from api.jobs import RunJobManager
from api.limits import RateLimiter
from fastapi.testclient import TestClient
from sparkplan.llm.phase3 import FakeLLM
from sparkplan.models.catalog import get_catalog
from sparkplan.pipeline.run_models import (
    RunRefusal,
    RunResult,
    RunStageResult,
    RunStatus,
    StageStatus,
)
from sparkplan.pipeline.run_paths import RunPaths
from tests.pipeline.test_run import fake_compile, fake_geometry, fake_slice
from tests.planner.phase3.conftest import valid_spec

run_module = importlib.import_module("sparkplan.pipeline.run")

STAGE_NAMES = (
    "plan",
    "validation",
    "geometry",
    "slicing",
    "firmware_generation",
    "firmware_compile",
    "guide",
)

ARTIFACT_BYTES = {
    "base_stl": b"solid base\nendsolid base\n",
    "lid_stl": b"solid lid\nendsolid lid\n",
    "assembled_stl": b"solid assembled\nendsolid assembled\n",
    "sketch": b"void setup() {}\nvoid loop() {}\n",
    "guide": b"# Build guide\n\nNot physically built.\n",
}


class DeferredExecutor:
    """An executor that exposes jobs for explicit, deterministic execution."""

    def __init__(self) -> None:
        self.tasks: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []

    def submit(self, fn: Any, *args: Any, **kwargs: Any) -> object:
        self.tasks.append((fn, args, kwargs))
        return object()

    def run_next(self) -> None:
        fn, args, kwargs = self.tasks.pop(0)
        fn(*args, **kwargs)

    def shutdown(self, *, wait: bool = False) -> None:
        return None


def make_result(
    run_id: str = "run_1",
    *,
    status: RunStatus = RunStatus.OK,
    artifacts: dict[str, str] | None = None,
    refusal: RunRefusal | None = None,
) -> RunResult:
    if status is RunStatus.OK:
        stages = [RunStageResult(name=name, status=StageStatus.PASSED) for name in STAGE_NAMES]
    else:
        stages = [RunStageResult(name="plan", status=StageStatus.FAILED)]
        stages.extend(
            RunStageResult(
                name=name,
                status=StageStatus.SKIPPED,
                reason="not reached after the plan stage",
            )
            for name in STAGE_NAMES[1:]
        )
    return RunResult(
        run_id=run_id,
        status=status,
        stages=stages,
        artifacts=artifacts or {},
        refusal=refusal,
    )


def persist_all(root: Path, run_id: str = "run_1") -> tuple[RunResult, RunPaths]:
    paths = RunPaths(run_id, root).claim()
    paths.write_json("spec", {"status": "ok"})
    for name, data in ARTIFACT_BYTES.items():
        paths.write_bytes(name, data)
    inventory = paths.artifact_inventory()
    artifacts = {
        name: inventory[name]
        for name in ("spec", "base_stl", "lid_stl", "assembled_stl", "sketch", "guide")
    }
    artifacts["report"] = inventory["report"]
    artifacts["trace"] = inventory["trace"]
    result = make_result(run_id, artifacts=artifacts)
    paths.write_json("trace", {"schema_version": "1.0", "verification": {}})
    paths.write_json("report", result)
    return result, paths


_DEFAULT_LLM = object()


def client_for(
    tmp_path: Path,
    *,
    runner: Any | None = None,
    llm: Any = _DEFAULT_LLM,
    manager: RunJobManager | None = None,
    limiter: RateLimiter | None = None,
    cors_origins: list[str] | None = None,
) -> TestClient:
    selected_runner = runner or (lambda *args, **kwargs: make_result())
    app = create_app(
        canonical_llm=FakeLLM(script=[]) if llm is _DEFAULT_LLM else llm,
        canonical_catalog=get_catalog(),
        runs_root=tmp_path / "runs",
        canonical_run=selected_runner,
        job_manager=manager,
        rate_limiter=limiter or RateLimiter(limit=1000, window_s=60),
        cors_origins=cors_origins,
    )
    return TestClient(app)


def test_health_and_catalog_are_safe(tmp_path: Path) -> None:
    client = client_for(tmp_path)

    assert client.get("/health").json() == {"status": "ok"}
    catalog = client.get("/catalog")
    assert catalog.status_code == 200
    body = catalog.json()
    assert body["board"]["id"]
    assert body["board"]["rails"]
    assert body["parts"]
    assert body["capabilities"]


def test_post_plan_is_nonblocking_and_polling_returns_running_then_final(
    tmp_path: Path,
) -> None:
    executor = DeferredExecutor()
    manager = RunJobManager(executor=executor, max_pending=4)
    calls: list[dict[str, Any]] = []

    def runner(idea: str, llm: object, *, run_id: str, catalog: Any, runs_root: Any) -> RunResult:
        calls.append({"idea": idea, "run_id": run_id, "catalog": catalog, "runs_root": runs_root})
        result, _ = persist_all(tmp_path / "runs", run_id)
        return result

    client = client_for(tmp_path, runner=runner, manager=manager)

    response = client.post("/plan", json={"idea": "a desk plant monitor"})

    assert response.status_code == 202
    run_id = response.json()["run_id"]
    assert response.headers["location"] == f"/runs/{run_id}"
    assert calls == []

    running = client.get(f"/runs/{run_id}")
    assert running.status_code == 200
    assert running.json()["status"] == "running"
    assert running.json()["stages"] == []

    executor.run_next()
    final = client.get(f"/runs/{run_id}")
    assert final.status_code == 200
    assert final.json()["status"] == "ok"
    assert calls[0]["run_id"] == run_id


def test_worker_failure_is_a_safe_failed_result(tmp_path: Path) -> None:
    executor = DeferredExecutor()
    manager = RunJobManager(executor=executor)

    def runner(*args: Any, **kwargs: Any) -> RunResult:
        raise RuntimeError("secret token /tmp/private")

    client = client_for(tmp_path, runner=runner, manager=manager)
    response = client.post("/plan", json={"idea": "a desk plant monitor"})
    run_id = response.json()["run_id"]
    executor.run_next()

    detail = client.get(f"/runs/{run_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "failed"
    assert body["issues"][0]["code"] == "RUN_WORKER_FAILED"
    assert "secret" not in detail.text
    assert "/tmp/private" not in detail.text


def test_unsupported_run_exposes_planner_refusal_metadata(tmp_path: Path) -> None:
    executor = DeferredExecutor()
    manager = RunJobManager(executor=executor)
    refusal = RunRefusal(
        reasons=["SparkPlan has no flight controller."],
        missing_capabilities=["flight_control", "imu_stabilization"],
        closest_supported_alternative="A ground rover using DC motors.",
    )

    def runner(*args: Any, **kwargs: Any) -> RunResult:
        return make_result(
            kwargs["run_id"],
            status=RunStatus.UNSUPPORTED,
            refusal=refusal,
        )

    client = client_for(tmp_path, runner=runner, manager=manager)
    response = client.post("/plan", json={"idea": "an autonomous drone"})
    run_id = response.json()["run_id"]
    executor.run_next()

    detail = client.get(f"/runs/{run_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "unsupported"
    assert body["refusal"] == {
        "reasons": ["SparkPlan has no flight controller."],
        "missing_capabilities": ["flight_control", "imu_stabilization"],
        "closest_supported_alternative": "A ground rover using DC motors.",
    }


def test_prompt_limit_and_extra_fields_are_validated(tmp_path: Path) -> None:
    client = client_for(tmp_path)

    accepted = client.post("/plan", json={"idea": "x" * 500})
    assert accepted.status_code == 202
    assert accepted.json()["run_id"]
    assert client.post("/plan", json={"idea": "x" * 501}).status_code == 422
    assert client.post("/plan", json={"idea": ""}).status_code == 422
    assert client.post("/plan", json={"idea": "ok", "run_id": "chosen"}).status_code == 422


def test_per_ip_rate_limit_returns_retry_after(tmp_path: Path) -> None:
    executor = DeferredExecutor()
    manager = RunJobManager(executor=executor)
    limiter = RateLimiter(limit=1, window_s=60, clock=lambda: 0.0)
    client = client_for(tmp_path, manager=manager, limiter=limiter)

    first = client.post("/plan", json={"idea": "a desk plant monitor"})
    second = client.post("/plan", json={"idea": "a desk plant monitor"})

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "rate_limited"
    assert second.headers["retry-after"] == "60"


def test_missing_llm_is_a_safe_503(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> object:
        raise RuntimeError("SPARKPLAN_LLM_API_KEY=super-secret")

    monkeypatch.setattr("api.canonical.make_llm_from_env", unavailable)
    client = client_for(tmp_path, llm=None)

    response = client.post("/plan", json={"idea": "a desk plant monitor"})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "llm_unavailable"
    assert "super-secret" not in response.text
    assert "SPARKPLAN_LLM_API_KEY" not in response.text


def test_files_are_allowlisted_and_report_and_trace_are_served(tmp_path: Path) -> None:
    persist_all(tmp_path / "runs")
    client = client_for(tmp_path)

    filenames = {
        "base_stl": "base.stl",
        "lid_stl": "lid.stl",
        "assembled_stl": "assembled.stl",
        "sketch": "sketch.ino",
        "guide": "GUIDE.md",
    }
    for name, data in ARTIFACT_BYTES.items():
        response = client.get(f"/runs/run_1/files/{filenames[name]}")
        assert response.status_code == 200, name
        assert response.content == data
        assert response.headers["x-content-type-options"] == "nosniff"

    assert json.loads(client.get("/runs/run_1/files/spec.json").content) == {"status": "ok"}
    assert client.get("/runs/run_1/files/report.json").status_code == 200
    assert client.get("/runs/run_1/files/trace.json").status_code == 200
    assert client.get("/runs/run_1/files/secret.txt").status_code == 404
    assert client.get("/runs/run_1/files/..%2Freport.json").status_code == 404


def test_symlinked_file_is_not_served(tmp_path: Path) -> None:
    _, paths = persist_all(tmp_path / "runs")
    outside = tmp_path / "outside.stl"
    outside.write_bytes(b"solid outside\nendsolid outside\n")
    paths.artifact("base_stl").unlink()
    os.symlink(outside, paths.artifact("base_stl"))

    client = client_for(tmp_path)
    response = client.get("/runs/run_1/files/base.stl")

    assert response.status_code == 404


def test_bundle_contains_only_allowlisted_files_with_safe_names(tmp_path: Path) -> None:
    persist_all(tmp_path / "runs")
    client = client_for(tmp_path)

    response = client.get("/runs/run_1/bundle.zip")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
    assert names == {
        "spec.json",
        "report.json",
        "trace.json",
        "base.stl",
        "lid.stl",
        "assembled.stl",
        "sketch/sketch.ino",
        "GUIDE.md",
    }
    assert all(".." not in name and not name.startswith("/") for name in names)


def test_completed_run_can_be_read_after_manager_restart(tmp_path: Path) -> None:
    persist_all(tmp_path / "runs")
    manager = RunJobManager()
    client = client_for(tmp_path, manager=manager)

    detail = client.get("/runs/run_1")

    assert detail.status_code == 200
    assert detail.json()["run_id"] == "run_1"
    assert client.get("/runs/run_1/files/GUIDE.md").status_code == 200


def test_unknown_run_and_invalid_run_id_are_safe_404(tmp_path: Path) -> None:
    client = client_for(tmp_path)

    for run_id in ("nope", "a.b", "a" * 65):
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "run_not_found"


def test_cors_allows_configured_vite_origin(tmp_path: Path) -> None:
    client = client_for(tmp_path, cors_origins=["http://localhost:5173"])

    response = client.get("/health", headers={"Origin": "http://localhost:5173"})
    blocked = client.get("/health", headers={"Origin": "http://evil.example"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in blocked.headers


def test_default_canonical_runner_end_to_end_through_root_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sparkplan.planner.phase3.planner as planner_module
    from sparkplan.geometry.generate import GeometryResult
    from sparkplan.geometry.layout import pack_enclosure

    catalog = get_catalog()

    def planner_geometry(spec, cat=None, config=None, out_dir=None):
        return GeometryResult(
            layout=pack_enclosure(spec, cat or catalog)[0],
            issues=[],
            engine="test",
        )

    monkeypatch.setattr(planner_module, "generate_enclosure", planner_geometry)
    monkeypatch.setattr(run_module, "generate_enclosure", fake_geometry(catalog))
    monkeypatch.setattr(run_module, "verify_slice", fake_slice())
    monkeypatch.setattr(run_module, "compile_firmware", fake_compile())

    executor = DeferredExecutor()
    manager = RunJobManager(executor=executor)
    app = create_app(
        canonical_llm=FakeLLM(script=[valid_spec()]),
        canonical_catalog=catalog,
        runs_root=tmp_path / "runs",
        job_manager=manager,
        rate_limiter=RateLimiter(limit=100, window_s=60),
    )
    client = TestClient(app)

    started = client.post("/plan", json={"idea": "a desk plant monitor"})
    assert started.status_code == 202
    run_id = started.json()["run_id"]
    executor.run_next()

    result = client.get(f"/runs/{run_id}")
    assert result.status_code == 200
    body = result.json()
    assert body["status"] == "ok"
    assert [stage["status"] for stage in body["stages"]] == ["passed"] * 7

    guide = client.get(f"/runs/{run_id}/files/GUIDE.md")
    assert guide.status_code == 200
    assert "| Validated | true |" in guide.text
    assert "| Compiled | true |" in guide.text
    assert "| Sliced | true |" in guide.text
    assert "| Simulated | skipped |" in guide.text
    assert guide.text.endswith("Not physically built.")

    bundle = client.get(f"/runs/{run_id}/bundle.zip")
    assert bundle.status_code == 200
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
        assert "trace.json" in archive.namelist()
        assert "sketch/sketch.ino" in archive.namelist()
