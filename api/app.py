"""FastAPI application.

The LLM is chosen by environment (or injected in tests). Builds are kept
in memory: id -> PipelineResult.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field
from sparkplan.catalog import Catalog, load_catalog
from sparkplan.llm.client import LLMClient, make_llm_from_env
from sparkplan.models.spec import PlanResult
from sparkplan.pipeline import PipelineResult, run_pipeline
from sparkplan.planner import Planner


class PlanRequest(BaseModel):
    idea: str = Field(min_length=1, max_length=2000)


class BuildStore:
    def __init__(self) -> None:
        self._items: dict[str, PipelineResult] = {}

    def put(self, result: PipelineResult) -> str:
        build_id = uuid.uuid4().hex[:12]
        self._items[build_id] = result
        return build_id

    def get(self, build_id: str) -> PipelineResult | None:
        return self._items.get(build_id)

    def __len__(self) -> int:
        return len(self._items)


def _jsonable(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "value"):  # Enum
        return obj.value
    return obj


def create_app(llm: LLMClient | None = None, catalog: Catalog | None = None) -> FastAPI:
    app = FastAPI(title="SparkPlan", version="0.1.0")
    store = BuildStore()
    cat = catalog or load_catalog()
    llm_holder: dict[str, LLMClient] = {"llm": llm} if llm is not None else {}

    def _require_llm() -> LLMClient:
        if "llm" not in llm_holder:
            llm_holder["llm"] = make_llm_from_env()
        return llm_holder["llm"]

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/catalog")
    def get_catalog() -> dict[str, Any]:
        return {
            "controller": cat.power.controller,
            "parts": [
                {
                    "part_id": p.part_id,
                    "name": p.name,
                    "category": p.category.value,
                    "rail": p.supply.rail,
                    "pins": [pin.name for pin in p.pins],
                    "api": [a.name for a in p.firmware.api],
                }
                for p in cat.parts.values()
            ],
            "capabilities": [c.id for c in cat.capabilities.capabilities],
        }

    @app.post("/v1/plan")
    def post_plan(req: PlanRequest) -> dict[str, Any]:
        try:
            planner = Planner(_require_llm(), cat)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        result: PlanResult = planner.plan(req.idea)
        return _jsonable(result)

    @app.post("/v1/build")
    def post_build(req: PlanRequest) -> dict[str, Any]:
        try:
            result = run_pipeline(req.idea, _require_llm(), catalog=cat)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        build_id = store.put(result)
        summary: dict[str, Any] = {
            "id": build_id,
            "status": result.status,
            "refusal_reasons": result.refusal_reasons,
            "issues": [i.model_dump() for i in result.issues],
        }
        if result.spec is not None:
            summary["spec"] = result.spec.model_dump()
        if result.firmware is not None:
            summary["libraries"] = result.firmware.libraries
        if result.enclosure_plan is not None:
            summary["enclosure"] = result.enclosure_plan.params.model_dump(
                include={"inner_x_mm", "inner_y_mm", "inner_z_mm", "wall_mm"}
            )
        return summary

    def _get_build(build_id: str) -> PipelineResult:
        result = store.get(build_id)
        if result is None:
            raise HTTPException(status_code=404, detail="unknown build id")
        return result

    @app.get("/v1/builds/{build_id}")
    def get_build(build_id: str) -> dict[str, Any]:
        result = _get_build(build_id)
        summary: dict[str, Any] = {
            "id": build_id,
            "status": result.status,
            "idea": result.idea,
            "refusal_reasons": result.refusal_reasons,
            "issues": [i.model_dump() for i in result.issues],
        }
        if result.spec is not None:
            summary["spec"] = result.spec.model_dump()
        return summary

    @app.get("/v1/builds/{build_id}/enclosure/{part}.stl")
    def get_stl(build_id: str, part: str) -> Response:
        result = _get_build(build_id)
        if result.enclosure is None or part not in ("base", "lid"):
            raise HTTPException(status_code=404, detail="no such STL for this build")
        mesh = result.enclosure.base_trimesh() if part == "base" else result.enclosure.lid_trimesh()
        data: bytes = mesh.export(file_type="stl")
        return Response(content=data, media_type="model/stl")

    @app.get("/v1/builds/{build_id}/firmware/sparkplan.ino")
    def get_sketch(build_id: str) -> Response:
        result = _get_build(build_id)
        if result.firmware is None:
            raise HTTPException(status_code=404, detail="no firmware for this build")
        return Response(content=result.firmware.sketch, media_type="text/plain")

    @app.get("/v1/builds/{build_id}/guide.md")
    def get_guide(build_id: str) -> Response:
        result = _get_build(build_id)
        if result.guide is None:
            raise HTTPException(status_code=404, detail="no guide for this build")
        return Response(content=result.guide, media_type="text/markdown")

    return app


app = create_app()
