"""Provider-agnostic LLM access.

The LLM's only job is to emit JSON matching a schema (the BuildSpec).
`complete_json` raises `LLMError` on transport or format failures; the
planner decides how to repair.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx


class LLMError(RuntimeError):
    """Transport or response-format failure."""


@runtime_checkable
class LLMClient(Protocol):
    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return a JSON object validated against `schema` (best-effort)."""
        ...


@dataclass
class FakeLLM:
    """Scripted responses for tests. No network, ever.

    Each script entry is either a dict (returned as-is) or an Exception
    (raised). When the script runs dry, `last` is replayed forever, which
    makes "bad idea" scenarios easy to express.
    """

    script: list[dict[str, Any] | Exception] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"system": system, "user": user, "schema": schema})
        if not self.script:
            raise LLMError("FakeLLM script exhausted")
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return json.loads(json.dumps(item))  # deep copy, caller may mutate


@dataclass
class OpenAICompatClient:
    """Minimal adapter for any OpenAI-compatible /chat/completions endpoint.

    Configure with:
      SPARKPLAN_LLM_BASE_URL  (e.g. https://api.openai.com/v1 or https://api.groq.com/openai/v1)
      SPARKPLAN_LLM_API_KEY
      SPARKPLAN_LLM_MODEL     (e.g. gpt-4o-mini or llama-3.3-70b-versatile)
    """

    base_url: str
    api_key: str
    model: str
    timeout_s: float = 60.0
    temperature: float = 0.0

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": schema, "strict": True},
            },
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            resp = httpx.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc
        try:
            content = resp.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise LLMError(f"unexpected LLM response shape: {exc}") from exc


def make_llm_from_env() -> LLMClient:
    """Factory selected by SPARKPLAN_LLM_PROVIDER.

    Defaults to a real OpenAI-compatible client when a base URL is set,
    otherwise refuses loudly - unit tests must inject FakeLLM explicitly.
    """
    provider = os.environ.get("SPARKPLAN_LLM_PROVIDER", "").strip().lower()
    if provider in ("", "openai_compat", "openai", "groq"):
        base_url = os.environ.get("SPARKPLAN_LLM_BASE_URL", "").strip()
        api_key = os.environ.get("SPARKPLAN_LLM_API_KEY", "").strip()
        model = os.environ.get("SPARKPLAN_LLM_MODEL", "").strip()
        if base_url and api_key and model:
            return OpenAICompatClient(base_url=base_url, api_key=api_key, model=model)
        raise LLMError(
            "SPARKPLAN_LLM_PROVIDER requires SPARKPLAN_LLM_BASE_URL, "
            "SPARKPLAN_LLM_API_KEY and SPARKPLAN_LLM_MODEL to be set"
        )
    if provider == "fake":
        return FakeLLM(script=[])
    raise LLMError(f"unknown SPARKPLAN_LLM_PROVIDER {provider!r}")
