from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
from api.app import create_app
from fastapi.testclient import TestClient
from sparkplan.llm.phase3 import FakeLLM
from sparkplan.models.catalog import get_catalog
from sparkplan.pipeline.run_models import (
    RunResult,
    RunStageResult,
    RunStatus,
    StageStatus,
)
from sparkplan.pipeline.run_paths import RunPaths
from tests.pipeline.test_run import fake_compile, fake_geometry, fake_slice
from tests.planner.phase3.conftest import valid_spec

run_module = importlib.import_module("sparkplan.pipeline.run")

ARTIFACT_BYTES: dict[str, bytes] = {
    "spec": b'{"status": "ok"}\n',
    "base_stl": b"solid base\nendsolid base\n",
    "lid_stl": b"solid lid\nendsolid lid\n",
    "assembled_stl": b"solid assembled\nendsolid assembled\n",
    "sketch": b"void setup() {}\nvoid loop() {}\n",
    "guide": b"# Build guide\n",
}

MEDIA_TYPES = {
    "spec": "application/json",
    "base_stl": "model/stl",
    "lid_stl": "model/stl",
    "assembled_stl": "model/stl",
    "sketch": "text/plain",
    "guide": "text/markdown",
}

FILENAMES = {
    "spec": "spec.json",
    "base_stl": "base.stl",
    "lid_stl": "lid.stl",
    "assembled_stl": "assembled.stl",
    "sketch": "sketch.ino",
    "guide": "GUIDE.md",
}

STAGE_NAMES = (
    "plan",
    "validation",
    "geometry",
    "slicing",
    "firmware_generation",
    "firmware_compile",
    "guide",
)


def make_result(
    run_id: str = "run_1",
    *,
    status: RunStatus = RunStatus.OK,
    artifacts: dict[str, str] | None = None,
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
    )


def persist_result(
    root: Path,
    result: RunResult,
    *,
    artifact_names: tuple[str, ...] = (),
) -> RunPaths:
    paths = RunPaths(result.run_id, root).claim()
    for name in artifact_names:
        if name == "spec":
            paths.write_json("spec", {"status": "ok"})
        else:
            paths.write_bytes(name, ARTIFACT_BYTES[name])
    paths.write_json("report", result)
    return paths


_DEFAULT_LLM = object()


def client_for(
    tmp_path: Path,
    *,
    runner: Any | None = None,
    canonical_llm: Any = _DEFAULT_LLM,
) -> TestClient:
    llm = FakeLLM(script=[]) if canonical_llm is _DEFAULT_LLM else canonical_llm
    app = create_app(
        canonical_llm=llm,
        canonical_catalog=get_catalog(),
        runs_root=tmp_path / "runs",
        canonical_run=runner or (lambda *args, **kwargs: make_result()),
    )
    return TestClient(app)


def test_healthz_is_unchanged(tmp_path: Path) -> None:
    client = client_for(tmp_path)

    assert client.get("/healthz").json() == {"status": "ok"}


def test_post_run_returns_run_result_and_location(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    catalog = get_catalog()
    sentinel = object()

    def runner(idea: str, llm: object, *, catalog: Any, runs_root: Any) -> RunResult:
        result = make_result()
        persist_result(tmp_path / "runs", result)
        calls.append(
            {
                "idea": idea,
                "llm": llm,
                "catalog": catalog,
                "runs_root": runs_root,
            }
        )
        return result

    client = client_for(tmp_path, runner=runner, canonical_llm=sentinel)

    response = client.post("/v2/runs", json={"idea": "a desk weather station"})

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == "run_1"
    assert body["status"] == "ok"
    assert body["schema_version"] == "1.0"
    assert response.headers["location"] == "/v2/runs/run_1"
    assert calls == [
        {
            "idea": "a desk weather station",
            "llm": sentinel,
            "catalog": catalog,
            "runs_root": tmp_path / "runs",
        }
    ]


def test_default_runner_persists_and_serves_canonical_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = get_catalog()
    monkeypatch.setattr(run_module, "generate_enclosure", fake_geometry(catalog))
    monkeypatch.setattr(run_module, "verify_slice", fake_slice())
    monkeypatch.setattr(run_module, "compile_firmware", fake_compile())
    app = create_app(
        canonical_llm=FakeLLM(script=[valid_spec()]),
        canonical_catalog=catalog,
        runs_root=tmp_path / "runs",
    )
    client = TestClient(app)

    response = client.post("/v2/runs", json={"idea": "a desk plant monitor"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert [stage["name"] for stage in body["stages"]] == list(STAGE_NAMES)
    assert response.headers["location"] == f"/v2/runs/{body['run_id']}"
    assert (tmp_path / "runs" / body["run_id"] / "report.json").is_file()

    detail = client.get(f"/v2/runs/{body['run_id']}")
    assert detail.status_code == 200
    assert detail.json()["run_id"] == body["run_id"]

    for name in ARTIFACT_BYTES:
        artifact = client.get(f"/v2/runs/{body['run_id']}/artifacts/{name}")
        assert artifact.status_code == 200, name
        assert artifact.content


@pytest.mark.parametrize(
    "status",
    [RunStatus.REFUSED, RunStatus.UNSUPPORTED, RunStatus.FAILED],
)
def test_terminal_domain_outcomes_return_200_without_location(
    tmp_path: Path,
    status: RunStatus,
) -> None:
    client = client_for(tmp_path, runner=lambda *args, **kwargs: make_result(status=status))

    response = client.post("/v2/runs", json={"idea": "a camera drone"})

    assert response.status_code == 200
    assert response.json()["status"] == status.value
    assert "location" not in response.headers


def test_get_run_loads_the_persisted_report(tmp_path: Path) -> None:
    result = make_result()
    persist_result(tmp_path / "runs", result)
    client = client_for(tmp_path)

    response = client.get("/v2/runs/run_1")

    assert response.status_code == 200
    assert response.json() == result.model_dump(mode="json")


def test_unknown_or_malformed_run_is_404(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    missing = client.get("/v2/runs/nope")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "run_not_found"

    paths = RunPaths("broken", tmp_path / "runs").claim()
    paths.artifact("report").write_text("{not json", encoding="utf-8")
    broken = client.get("/v2/runs/broken")
    assert broken.status_code == 404
    assert broken.json()["detail"]["code"] == "run_not_found"


def test_get_artifacts_are_allowlisted_and_typed(tmp_path: Path) -> None:
    names = tuple(ARTIFACT_BYTES)
    inventory = RunPaths("run_1", tmp_path / "runs").artifact_inventory()
    result = make_result(artifacts={name: inventory[name] for name in names})
    persist_result(tmp_path / "runs", result, artifact_names=names)
    client = client_for(tmp_path)

    for name in names:
        response = client.get(f"/v2/runs/run_1/artifacts/{name}")
        assert response.status_code == 200, name
        if name == "spec":
            assert json.loads(response.content) == {"status": "ok"}
        else:
            assert response.content == ARTIFACT_BYTES[name]
        assert response.headers["content-type"].startswith(MEDIA_TYPES[name])
        assert f'filename="{FILENAMES[name]}"' in response.headers["content-disposition"]
        assert response.headers["x-content-type-options"] == "nosniff"


def test_report_and_trace_are_not_artifact_roots(tmp_path: Path) -> None:
    result = make_result()
    paths = persist_result(tmp_path / "runs", result)
    (paths.run_dir / "trace.json").write_text("{}\n", encoding="utf-8")
    client = client_for(tmp_path)

    for name in ("report", "trace", "unknown", "../report"):
        response = client.get(f"/v2/runs/run_1/artifacts/{name}")
        assert response.status_code == 404, name


def test_missing_or_unsafe_artifact_is_404(tmp_path: Path) -> None:
    result = make_result(artifacts={"base_stl": "base.stl"})
    paths = persist_result(tmp_path / "runs", result)
    client = client_for(tmp_path)

    missing = client.get("/v2/runs/run_1/artifacts/base_stl")
    assert missing.status_code == 404

    outside = tmp_path / "outside.stl"
    outside.write_bytes(b"solid outside\nendsolid outside\n")
    os.symlink(outside, paths.artifact("base_stl"))
    unsafe = client.get("/v2/runs/run_1/artifacts/base_stl")
    assert unsafe.status_code == 404


def test_invalid_run_id_is_404(tmp_path: Path) -> None:
    client = client_for(tmp_path)

    for run_id in ("a.b", "a%20b", "a" * 65):
        assert client.get(f"/v2/runs/{run_id}").status_code == 404


def test_request_validation_rejects_empty_extra_and_oversized_ideas(tmp_path: Path) -> None:
    client = client_for(tmp_path)

    assert client.post("/v2/runs", json={"idea": ""}).status_code == 422
    assert client.post("/v2/runs", json={"idea": "x" * 2001}).status_code == 422
    assert client.post("/v2/runs", json={"idea": "ok", "run_id": "chosen"}).status_code == 422


def test_missing_llm_configuration_is_safe_503(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> object:
        raise RuntimeError("SPARKPLAN_LLM_API_KEY=super-secret")

    monkeypatch.setattr("api.v2.make_llm_from_env", unavailable)
    client = client_for(tmp_path, canonical_llm=None)

    response = client.post("/v2/runs", json={"idea": "a desk weather station"})

    assert response.status_code == 503
    body = response.text
    assert "llm_unavailable" in body
    assert "super-secret" not in body
    assert "SPARKPLAN_LLM_API_KEY" not in body


def test_unexpected_runner_failure_is_safe_500(tmp_path: Path) -> None:
    def runner(*args: object, **kwargs: object) -> RunResult:
        raise RuntimeError("secret /tmp/private/path")

    client = client_for(tmp_path, runner=runner, canonical_llm=FakeLLM(script=[]))

    response = client.post("/v2/runs", json={"idea": "a desk weather station"})

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "code": "internal_error",
            "message": "The run could not be completed safely.",
        }
    }
    assert "secret" not in response.text
    assert "/tmp/private" not in response.text


def test_run_id_is_generated_by_the_runner_not_the_request(tmp_path: Path) -> None:
    seen: list[str] = []

    def runner(idea: str, llm: object, **kwargs: object) -> RunResult:
        seen.append(idea)
        return make_result(run_id="generated_by_canonical_run")

    client = client_for(tmp_path, runner=runner, canonical_llm=FakeLLM(script=[]))

    response = client.post("/v2/runs", json={"idea": "a desk weather station"})

    assert response.status_code == 200
    assert response.json()["run_id"] == "generated_by_canonical_run"
    assert seen == ["a desk weather station"]


def test_get_run_does_not_serve_an_arbitrary_json_file(tmp_path: Path) -> None:
    client = client_for(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "report.json").write_text(
        json.dumps(make_result().model_dump(mode="json")),
        encoding="utf-8",
    )

    assert client.get("/v2/runs/outside").status_code == 404
