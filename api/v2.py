"""Canonical Phase 6 API.

This router is deliberately separate from the legacy ``/v1`` API. It executes
the canonical run synchronously and returns the persisted ``RunResult``
directly. Artifact downloads are limited to the documented logical names and
are resolved through ``RunPaths`` so no request can escape a run directory.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Final, TypeVar

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sparkplan.llm.phase3.client import (
    LLMClient,
    LLMConfigurationError,
    load_dotenv,
    make_llm_from_env,
)
from sparkplan.models.catalog import Catalog, get_catalog
from sparkplan.pipeline.run import run as run_canonical
from sparkplan.pipeline.run_models import RunResult
from sparkplan.pipeline.run_paths import RunPathError, RunPaths

CanonicalRunner = Callable[..., RunResult]

_T = TypeVar("_T")

ARTIFACT_NAMES: Final[tuple[str, ...]] = (
    "spec",
    "base_stl",
    "lid_stl",
    "assembled_stl",
    "sketch",
    "guide",
)

_ARTIFACT_RESPONSES: Final[dict[str, tuple[str, str]]] = {
    "spec": ("application/json", "spec.json"),
    "base_stl": ("model/stl", "base.stl"),
    "lid_stl": ("model/stl", "lid.stl"),
    "assembled_stl": ("model/stl", "assembled.stl"),
    "sketch": ("text/plain; charset=utf-8", "sketch.ino"),
    "guide": ("text/markdown; charset=utf-8", "GUIDE.md"),
}


class RunRequest(BaseModel):
    """The only input the canonical API accepts for a new run."""

    model_config = ConfigDict(extra="forbid")

    idea: str = Field(min_length=1, max_length=2000)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _guard(operation: Callable[[], _T]) -> _T:
    """Convert unexpected failures to a safe, consistent API error."""
    try:
        return operation()
    except HTTPException:
        raise
    except Exception:
        raise _error(
            500,
            "internal_error",
            "The run could not be completed safely.",
        ) from None


def _not_found(message: str = "unknown run id") -> HTTPException:
    return _error(404, "run_not_found", message)


def _load_result(run_id: str, runs_root: str | Path | None) -> tuple[RunResult, RunPaths]:
    """Load a persisted report without trusting the caller's path input."""
    try:
        paths = RunPaths(run_id, runs_root)
        if paths.run_dir.is_symlink() or not paths.run_dir.is_dir():
            raise _not_found()
        report = paths.artifact("report")
        if report.is_symlink() or not report.is_file():
            raise _not_found()
        text = report.read_text(encoding="utf-8")
        result = RunResult.model_validate_json(text)
    except HTTPException:
        raise
    except (OSError, RunPathError, ValueError):
        raise _not_found() from None
    if result.run_id != paths.run_id:
        raise _not_found()
    return result, paths


def _report_exists(run_id: str, runs_root: str | Path | None) -> bool:
    try:
        paths = RunPaths(run_id, runs_root)
        report = paths.artifact("report")
        return not report.is_symlink() and report.is_file()
    except (OSError, RunPathError):
        return False


def _read_artifact(
    run_id: str,
    artifact_name: str,
    runs_root: str | Path | None,
) -> Response:
    if artifact_name not in _ARTIFACT_RESPONSES:
        raise _error(404, "artifact_not_found", "unknown artifact")

    result, paths = _load_result(run_id, runs_root)
    if artifact_name not in result.artifacts:
        raise _error(404, "artifact_not_found", "artifact is not available for this run")
    if result.artifacts[artifact_name] != paths.artifact_inventory()[artifact_name]:
        raise _error(404, "artifact_not_found", "artifact is not available for this run")

    try:
        path = paths.artifact(artifact_name)
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            raise _error(404, "artifact_not_found", "artifact is not available for this run")
        data = path.read_bytes()
    except HTTPException:
        raise
    except (OSError, RunPathError):
        raise _error(404, "artifact_not_found", "artifact is not available for this run") from None
    if not data:
        raise _error(404, "artifact_not_found", "artifact is not available for this run")

    media_type, filename = _ARTIFACT_RESPONSES[artifact_name]
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def create_v2_router(
    *,
    llm: LLMClient | None = None,
    catalog: Catalog | None = None,
    runs_root: str | Path | None = None,
    runner: CanonicalRunner | None = None,
) -> APIRouter:
    """Build the canonical router with injectable, offline-testable dependencies."""
    router = APIRouter(prefix="/v2", tags=["v2"])
    canonical_catalog = catalog if catalog is not None else get_catalog()
    execute = runner if runner is not None else run_canonical
    llm_holder: dict[str, LLMClient] = {"llm": llm} if llm is not None else {}

    def _require_llm() -> LLMClient:
        if "llm" not in llm_holder:
            try:
                load_dotenv()
                llm_holder["llm"] = make_llm_from_env()
            except LLMConfigurationError as exc:
                raise _error(
                    503,
                    "llm_configuration_error",
                    f"The canonical LLM is not configured correctly: {exc}",
                ) from None
            except Exception:
                raise _error(
                    503,
                    "llm_unavailable",
                    "The canonical LLM is not configured correctly.",
                ) from None
        return llm_holder["llm"]

    def _run_kwargs() -> dict[str, Any]:
        return {} if runs_root is None else {"runs_root": runs_root}

    @router.post("/runs", response_model=RunResult)
    def create_run(request: RunRequest, response: Response) -> RunResult:
        def operation() -> RunResult:
            result = execute(
                request.idea,
                _require_llm(),
                catalog=canonical_catalog,
                **_run_kwargs(),
            )
            if not isinstance(result, RunResult):
                raise TypeError("canonical runner must return RunResult")
            if _report_exists(result.run_id, runs_root):
                response.headers["Location"] = f"/v2/runs/{result.run_id}"
            return result

        return _guard(operation)

    @router.get("/runs/{run_id}", response_model=RunResult)
    def get_run(run_id: str) -> RunResult:
        def operation() -> RunResult:
            result, _ = _load_result(run_id, runs_root)
            return result

        return _guard(operation)

    @router.get("/runs/{run_id}/artifacts/{artifact_name}")
    def get_artifact(run_id: str, artifact_name: str) -> Response:
        return _guard(lambda: _read_artifact(run_id, artifact_name, runs_root))

    return router


__all__ = ["ARTIFACT_NAMES", "CanonicalRunner", "RunRequest", "create_v2_router"]
