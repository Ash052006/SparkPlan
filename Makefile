.PHONY: test lint format eval run-api

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run black --check .

format:
	uv run ruff check --fix .
	uv run black .

eval:
	uv run python -m eval.run_eval

run-api:
	uv run uvicorn api.app:app --reload
