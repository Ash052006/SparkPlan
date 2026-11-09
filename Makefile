.PHONY: test lint eval run-api wokwi-smoke

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run black --check .

eval:
	uv run python -m eval.run_eval

run-api:
	uv run uvicorn api.app:app --reload --host 127.0.0.1 --port 8001

wokwi-smoke:
	uv run python scripts/wokwi_smoke.py
