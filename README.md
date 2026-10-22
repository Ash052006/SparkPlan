# SparkPlan

Turn a plain-language hardware idea into a validated, buildable package:

1. **Validated parts list and wiring** — checked deterministically against a fixed catalog.
2. **3D-printable enclosure (STL)** — parametric CadQuery geometry driven by catalog dimensions.
3. **Firmware that compiles** — assembled from per-part templates in the catalog (arduino-cli).
4. **Build guide** — deterministic Markdown.

Target hardware is the **ESP32 DevKitC V4** plus a catalog of ~17 parts. Nothing
outside that catalog is ever recommended.

## Architecture rules

1. The LLM only produces a structured `BuildSpec` (Pydantic). It never writes geometry or firmware.
2. Everything the LLM outputs is checked by deterministic Python validators. Failures go back to the LLM as machine-readable errors, max 3 repair rounds.
3. Geometry comes from CadQuery code driven by catalog dimensions. Firmware comes from per-part templates stored in the catalog.
4. Unsupported ideas are refused with reasons. Never approximate an unsupported idea.
5. Every stage is its own package with its own tests and runs without the others.

## Layout

```
catalog/            parts/*.json + power.json + esp32_gpio.json + capabilities.json
sparkplan/
  models/           BuildSpec, Issue, Refusal (Pydantic v2)
  catalog/          catalog loader (validates every part at load)
  llm/              LLMClient protocol, FakeLLM, OpenAI-compatible adapter
  validators/       structure, pins, power, actions -> list[Issue]
  planner/          idea -> BuildSpec, with the repair loop and refusal path
  geometry/         enclosure params + CadQuery engine (build123d fallback)
  firmware/         sketch generation + arduino-cli compile
  guide/            deterministic Markdown build guide
  pipeline/         stage orchestration
api/                FastAPI app
eval/               eval harness + cases.json
web/                React + Vite + three.js viewer
tests/              per-stage pytest suites (no network; FakeLLM only)
```

Units everywhere are **millimetres, volts, milliamps**.

## Setup

```sh
brew install uv                       # or: pip install uv
uv python install 3.11
uv sync --extra dev
```

The preferred geometry engine is CadQuery. If its OCP stack cannot be
installed, `uv sync --extra build123d` installs build123d instead; the
geometry package picks whichever is importable. Do not install both — the
`cadquery-ocp-novtk` package that build123d pulls in breaks `import cadquery`.

## Usage

### Python

```python
from sparkplan.llm import make_llm_from_env
from sparkplan.pipeline import run_pipeline

result = run_pipeline("plant pot that beeps when the soil is dry", make_llm_from_env(), out_dir="out/plant")
print(result.status, result.firmware.libraries)
```

### API

The canonical root API runs plans in bounded background workers and is polled:

```sh
set -a
. ./.env
set +a
uv run uvicorn api.app:app --reload
```

* `POST /plan` — `{"idea": "..."}` (maximum 500 characters) -> `202` and
  `{"run_id": "..."}`. The request returns before planning starts.
* `GET  /runs/{run_id}` — poll for `status: "running"` or the final
  `RunResult`.
* `GET  /runs/{run_id}/files/{filename}` — download one allowlisted file:
  `spec.json`, `report.json`, `trace.json`, `base.stl`, `lid.stl`,
  `assembled.stl`, `sketch.ino`, or `GUIDE.md`.
* `GET  /runs/{run_id}/bundle.zip` — all available allowlisted files in one ZIP.
* `GET  /catalog` — canonical catalog summary.
* `GET  /health` — canonical health check; `/healthz` remains for compatibility.

`POST /plan` is limited to 10 requests per 60 seconds per client IP by default
(`SPARKPLAN_RATE_LIMIT`, `SPARKPLAN_RATE_WINDOW_S`). CORS allows the Vite
development origins by default (`SPARKPLAN_CORS_ORIGINS`). Invalid requests
return `422`, unknown runs/files return `404`, rate limiting returns `429` with
`Retry-After`, a full job queue or missing LLM configuration returns `503`, and
unexpected failures return a safe error envelope without internal exception
text. Completed runs can be read from `report.json` after a restart; refused or
unsupported runs have no artifacts and are only retained in memory.

The synchronous `/v2` API remains available for compatibility:

* `POST /v2/runs` — `{"idea": "..."}` -> `RunResult` (synchronous)
* `GET  /v2/runs/{run_id}` — persisted `RunResult`
* `GET  /v2/runs/{run_id}/artifacts/{name}` — download an available artifact

Legacy API (kept for compatibility):

* `GET  /v1/catalog`
* `POST /v1/plan`   — `{"idea": "..."}`
* `POST /v1/build`  — `{"idea": "..."}` -> build id
* `GET  /v1/builds/{id}`
* `GET  /v1/builds/{id}/enclosure/base.stl` (and `/lid.stl`)
* `GET  /v1/builds/{id}/firmware/sparkplan.ino`
* `GET  /v1/builds/{id}/guide.md`

### Web

The single-page UI polls the canonical `/plan` and `/runs/{id}` API, renders the
validation, STL enclosure, firmware and Markdown guide, and downloads the run
bundle. It respects the OS light/dark preference and has a manual toggle.

```sh
cd web
npm install
npm run dev        # proxies /plan, /runs, /catalog and health routes to localhost:8000
```

Checks, including the mobile Playwright smoke tests, use mocked API responses and
need no running backend:

```sh
npm run typecheck
npm run build
npx playwright install chromium
npm run test:e2e
```

## LLM configuration

Provider-agnostic, OpenAI-compatible endpoint:

```sh
export SPARKPLAN_LLM_PROVIDER=openai_compat   # or groq; both share this adapter
export SPARKPLAN_LLM_BASE_URL=https://api.groq.com/openai/v1
export SPARKPLAN_LLM_API_KEY=...
export SPARKPLAN_LLM_MODEL=llama-3.3-70b-versatile
export SPARKPLAN_LLM_MAX_TOKENS=1800  # multi-sensor specs need ~1.2k
```

Unit tests never touch the network: they inject `FakeLLM` with scripted
responses.

## Tests and linting

```sh
uv run ruff check .
uv run black --check .
uv run pytest
```

## Eval

```sh
uv run python -m eval.run_eval --out report.json
```

Refusal cases pass with no LLM call (deterministic keyword prefilter).
Supported cases need a configured LLM.
