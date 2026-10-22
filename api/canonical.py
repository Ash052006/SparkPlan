"""Canonical asynchronous root API.

``POST /plan`` only registers a bounded background job. Clients poll
``GET /runs/{run_id}`` for a running placeholder or the final ``RunResult``.
The older ``/v1`` and synchronous ``/v2`` routes remain available.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, TypeVar

from api.jobs import JobQueueFull, RunJobManager
from api.limits import RateLimiter
from api.v2 import CanonicalRunner
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sparkplan.llm.phase3.client import LLMClient, make_llm_from_env
from sparkplan.models.catalog import Catalog, get_catalog
from sparkplan.pipeline.run import run as run_canonical
from sparkplan.pipeline.run_models import RunResult, RunStatus
from sparkplan.pipeline.run_paths import RunPathError, RunPaths

_T = TypeVar("_T")

MAX_IDEA_LENGTH: Final = 500


class PlanRequest(BaseModel):
    """One plain-language hardware idea."""

    model_config = ConfigDict(extra="forbid")

    idea: str = Field(min_length=1, max_length=MAX_IDEA_LENGTH)


@dataclass(frozen=True)
class _Download:
    logical: str
    media_type: str
    filename: str
    archive_name: str


_DOWNLOADS: Final[dict[str, _Download]] = {
    "spec.json": _Download("spec", "application/json", "spec.json", "spec.json"),
    "report.json": _Download("report", "application/json", "report.json", "report.json"),
    "trace.json": _Download("trace", "application/json", "trace.json", "trace.json"),
    "base.stl": _Download("base_stl", "model/stl", "base.stl", "base.stl"),
    "lid.stl": _Download("lid_stl", "model/stl", "lid.stl", "lid.stl"),
    "assembled.stl": _Download("assembled_stl", "model/stl", "assembled.stl", "assembled.stl"),
    "sketch.ino": _Download(
        "sketch",
        "text/plain; charset=utf-8",
        "sketch.ino",
        "sketch/sketch.ino",
    ),
    "GUIDE.md": _Download("guide", "text/markdown; charset=utf-8", "GUIDE.md", "GUIDE.md"),
}

_ARCHIVE_TIMESTAMP: Final = (1980, 1, 1, 0, 0, 0)


def _error(
    status_code: int,
    code: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
        headers=headers,
    )


def _guard(operation: Any) -> Any:
    try:
        return operation()
    except HTTPException:
        raise
    except Exception:
        raise _error(500, "internal_error", "The request could not be completed safely.") from None


def _not_found(message: str = "unknown run id") -> HTTPException:
    return _error(404, "run_not_found", message)


def _load_persisted(run_id: str, runs_root: str | Path | None) -> tuple[RunResult, RunPaths]:
    try:
        paths = RunPaths(run_id, runs_root)
        if paths.run_dir.is_symlink() or not paths.run_dir.is_dir():
            raise _not_found()
        report = paths.artifact("report")
        if report.is_symlink() or not report.is_file():
            raise _not_found()
        result = RunResult.model_validate_json(report.read_text(encoding="utf-8"))
    except HTTPException:
        raise
    except (OSError, RunPathError, ValueError):
        raise _not_found() from None
    if result.run_id != paths.run_id:
        raise _not_found()
    return result, paths


def _resolve_result(
    run_id: str,
    runs_root: str | Path | None,
    jobs: RunJobManager,
) -> tuple[RunResult, RunPaths | None]:
    known = jobs.get(run_id)
    if known is not None:
        return known, None
    result, paths = _load_persisted(run_id, runs_root)
    return result, paths


def _paths_for(run_id: str, runs_root: str | Path | None, known: RunPaths | None) -> RunPaths:
    if known is not None:
        return known
    try:
        return RunPaths(run_id, runs_root)
    except RunPathError:
        raise _not_found() from None


def _download_path(
    result: RunResult,
    paths: RunPaths,
    download: _Download,
) -> Path | None:
    if result.status is RunStatus.RUNNING:
        return None
    expected = paths.artifact_inventory().get(download.logical)
    if expected is None or result.artifacts.get(download.logical) != expected:
        return None
    try:
        path = paths.artifact(download.logical)
    except RunPathError:
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        return None
    return path


def _read_download(
    run_id: str,
    name: str,
    runs_root: str | Path | None,
    jobs: RunJobManager,
) -> Response:
    download = _DOWNLOADS.get(name)
    if download is None:
        raise _error(404, "file_not_found", "unknown file")
    result, known_paths = _resolve_result(run_id, runs_root, jobs)
    paths = _paths_for(run_id, runs_root, known_paths)
    path = _download_path(result, paths, download)
    if path is None:
        raise _error(404, "file_not_found", "file is not available for this run")
    try:
        data = path.read_bytes()
    except OSError:
        raise _error(404, "file_not_found", "file is not available for this run") from None
    return Response(
        content=data,
        media_type=download.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{download.filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def _bundle(run_id: str, runs_root: str | Path | None, jobs: RunJobManager) -> Response:
    result, known_paths = _resolve_result(run_id, runs_root, jobs)
    paths = _paths_for(run_id, runs_root, known_paths)
    if result.status is RunStatus.RUNNING:
        raise _error(404, "file_not_found", "run is still in progress")

    files: list[tuple[str, bytes]] = []
    for download in _DOWNLOADS.values():
        path = _download_path(result, paths, download)
        if path is None:
            continue
        try:
            files.append((download.archive_name, path.read_bytes()))
        except OSError:
            continue
    if not files:
        raise _error(404, "file_not_found", "no files are available for this run")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for archive_name, data in files:
            info = zipfile.ZipInfo(archive_name, date_time=_ARCHIVE_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{result.run_id}-bundle.zip"',
            "X-Content-Type-Options": "nosniff",
        },
    )


def _catalog_payload(catalog: Catalog) -> dict[str, Any]:
    board = catalog.board()
    board_model = board.board
    return {
        "board": {
            "id": board.id,
            "name": board.name,
            "rails": (
                [
                    {
                        "name": rail.name,
                        "voltage_v": rail.voltage_v,
                        "max_current_ma": rail.max_current_ma,
                        "source_note": rail.source_note,
                        "approximate": rail.approximate,
                        "verify_against_datasheet": rail.verify_against_datasheet,
                    }
                    for rail in board_model.rails
                ]
                if board_model is not None
                else []
            ),
        },
        "parts": [
            {
                "id": part.id,
                "name": part.name,
                "category": part.category.value,
                "interface": part.interface.value,
                "mount": part.mount.value,
                "supply_voltage_v": {
                    "min": part.supply_voltage_v.min_v,
                    "max": part.supply_voltage_v.max_v,
                },
                "typical_current_ma": part.typical_current_ma,
                "peak_current_ma": part.peak_current_ma,
                "pins": [pin.name for pin in part.pins],
            }
            for part in catalog.parts
        ],
        "capabilities": catalog.all_capabilities(),
    }


def create_root_router(
    *,
    llm: LLMClient | None = None,
    catalog: Catalog | None = None,
    runs_root: str | Path | None = None,
    runner: CanonicalRunner | None = None,
    jobs: RunJobManager | None = None,
    rate_limiter: RateLimiter | None = None,
) -> APIRouter:
    """Build the canonical root router with injectable dependencies."""
    router = APIRouter(tags=["canonical"])
    canonical_catalog = catalog if catalog is not None else get_catalog()
    execute = runner if runner is not None else run_canonical
    manager = jobs if jobs is not None else RunJobManager()
    limiter = rate_limiter if rate_limiter is not None else RateLimiter.from_env()
    llm_holder: dict[str, LLMClient] = {"llm": llm} if llm is not None else {}

    def _require_llm() -> LLMClient:
        if "llm" not in llm_holder:
            try:
                llm_holder["llm"] = make_llm_from_env()
            except Exception:
                raise _error(
                    503,
                    "llm_unavailable",
                    "The canonical LLM is not configured correctly.",
                ) from None
        return llm_holder["llm"]

    def _run_kwargs() -> dict[str, Any]:
        return {} if runs_root is None else {"runs_root": runs_root}

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/catalog")
    def get_catalog_payload() -> dict[str, Any]:
        return _guard(lambda: _catalog_payload(canonical_catalog))

    @router.post("/plan", status_code=202)
    def start_plan(
        request: PlanRequest, http_request: Request, response: Response
    ) -> dict[str, str]:
        def operation() -> dict[str, str]:
            client_host = http_request.client.host if http_request.client else "unknown"
            retry_after = limiter.check(client_host)
            if retry_after is not None:
                raise _error(
                    429,
                    "rate_limited",
                    "Too many plan requests. Try again shortly.",
                    headers={"Retry-After": RateLimiter.retry_after_header(retry_after)},
                )
            selected_llm = _require_llm()

            def runner_for_job(idea: str, run_id: str) -> RunResult:
                result = execute(
                    idea,
                    selected_llm,
                    catalog=canonical_catalog,
                    run_id=run_id,
                    **_run_kwargs(),
                )
                if not isinstance(result, RunResult):
                    raise TypeError("canonical runner must return RunResult")
                return result

            try:
                run_id = manager.submit(request.idea, runner_for_job)
            except JobQueueFull:
                raise _error(
                    503,
                    "queue_full",
                    "The background run queue is full. Try again shortly.",
                ) from None
            response.headers["Location"] = f"/runs/{run_id}"
            return {"run_id": run_id}

        return _guard(operation)

    @router.get("/runs/{run_id}", response_model=RunResult)
    def get_run(run_id: str) -> RunResult:
        return _guard(lambda: _resolve_result(run_id, runs_root, manager)[0])

    @router.get("/runs/{run_id}/files/{name}")
    def get_file(run_id: str, name: str) -> Response:
        return _guard(lambda: _read_download(run_id, name, runs_root, manager))

    @router.get("/runs/{run_id}/bundle.zip")
    def get_bundle(run_id: str) -> Response:
        return _guard(lambda: _bundle(run_id, runs_root, manager))

    return router


__all__ = ["MAX_IDEA_LENGTH", "PlanRequest", "create_root_router"]
