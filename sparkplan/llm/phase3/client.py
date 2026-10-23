"""Phase 3 LLM access: a JSON-only client with usage accounting.

The LLM's single job is to emit JSON matching a schema (the Phase 1/2
BuildSpec). ``complete_json`` returns an :class:`LLMResponse` carrying the
parsed object *and* the provider-reported token usage. It raises
:class:`LLMError` (or the retryable subclass) on transport or format
failures; the planner decides how to repair.

This module is independent of the legacy ``sparkplan.llm.client``. Nothing
here imports or mutates that module; both coexist.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class LLMError(RuntimeError):
    """Transport or response-format failure that will not be retried."""


class LLMRetryableError(LLMError):
    """A transient failure (timeout, connection error, 429, 5xx).

    The client itself performs at most one transport retry for these; if the
    retry also fails the error surfaces to the planner as a transport failure.
    """


class LLMConfigurationError(LLMError):
    """Configuration failure (missing or invalid credentials, unknown provider, etc.).

    This error indicates misconfiguration rather than a runtime transport
    or format failure. It identifies the misconfigured field without
    exposing secret values.
    """


class LLMAuthenticationError(LLMError):
    """Authentication or authorization failure (HTTP 401/403) from provider.

    Indicates rejected credentials (e.g., invalid or revoked API key).
    """


class LLMQuotaOrRateLimitError(LLMRetryableError):
    """Provider rate limit or quota exceeded (HTTP 429)."""


# ---------------------------------------------------------------------------
# Response value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMUsage:
    """Token usage reported by the provider.

    ``present`` is False when the provider returned no usage block; in that
    case the counts are 0 and MUST NOT be treated as a real measurement.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    present: bool = False

    @classmethod
    def from_provider(cls, usage: object) -> LLMUsage:
        if not isinstance(usage, dict):
            return cls(present=False)
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if not isinstance(prompt, int) or not isinstance(completion, int):
            return cls(present=False)
        return cls(prompt_tokens=prompt, completion_tokens=completion, present=True)


# Convention: when the provider gives no usage we record ``present=False`` and
# zero counts, and accumulate nothing. We never estimate or invent tokens.
UNKNOWN_USAGE = LLMUsage()


@dataclass(frozen=True)
class LLMResponse:
    """One completion: the JSON object plus provider usage.

    ``finish_reason`` is the provider's stop reason (when reported). A value of
    ``"length"`` means the completion was cut off by the token cap and the JSON
    is very likely incomplete; callers must not treat that as a valid answer.
    """

    data: dict[str, Any]
    usage: LLMUsage = field(default_factory=LLMUsage)
    finish_reason: str | None = None

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"


@runtime_checkable
class LLMClient(Protocol):
    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> LLMResponse:
        """Return a JSON object (best-effort schema-conformant) plus usage."""
        ...


# ---------------------------------------------------------------------------
# Fake client (tests only, never network)
# ---------------------------------------------------------------------------


@dataclass
class FakeLLM:
    """Scripted responses for tests. No network, ever.

    Each script entry is one of:

    * a ``dict``                        -> returned with unknown usage
    * a ``(dict, LLMUsage)`` tuple      -> returned with the given usage
    * a ``(dict, None)`` tuple          -> returned with unknown usage
    * a ``(dict, usage, finish_reason)`` -> returned with the given stop reason
    * an ``Exception``                  -> raised

    When the script runs dry, the last entry is replayed forever, which makes
    "always fails" / "always bad" scenarios easy to express. Every call is
    recorded verbatim in ``calls``.
    """

    script: list[Any] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> LLMResponse:
        self.calls.append({"system": system, "user": user, "schema": schema})
        if not self.script:
            raise LLMError("FakeLLM script exhausted")
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        if isinstance(item, tuple):
            if len(item) == 3:
                data, usage, reason = item
                return LLMResponse(
                    data=json.loads(json.dumps(data)),
                    usage=usage or UNKNOWN_USAGE,
                    finish_reason=reason,
                )
            data, usage = item
            return LLMResponse(data=json.loads(json.dumps(data)), usage=usage or UNKNOWN_USAGE)
        return LLMResponse(data=json.loads(json.dumps(item)))


# ---------------------------------------------------------------------------
# Real OpenAI-compatible client
# ---------------------------------------------------------------------------


@dataclass
class OpenAICompatibleClient:
    """Minimal adapter for any OpenAI-compatible ``/chat/completions`` endpoint.

    Configured from the environment by :func:`make_llm_from_env`:

    * ``SPARKPLAN_LLM_BASE_URL``  e.g. https://api.openai.com/v1
    * ``SPARKPLAN_LLM_API_KEY``
    * ``SPARKPLAN_LLM_MODEL``     e.g. gpt-4o-mini
    * ``SPARKPLAN_LLM_TIMEOUT_S`` optional, default 60
    * ``SPARKPLAN_LLM_MAX_TOKENS`` optional, omitted when unset

    Structured output: the primary mode is a strict ``json_schema``. Not every
    OpenAI-compatible provider implements it, so a single fallback attempt with
    ``json_object`` is made if the provider rejects the schema mode. The
    returned payload is validated by the caller regardless of provider mode.

    Transport retries are separate from planner repair rounds: at most ONE
    extra HTTP attempt is made, and only for retryable failures.
    """

    base_url: str
    api_key: str
    model: str
    timeout_s: float = 60.0
    temperature: float = 0.0
    max_tokens: int | None = None
    max_transport_retries: int = 1
    backoff_base_s: float = 1.0
    max_backoff_s: float = 20.0
    http_client: httpx.Client | None = None

    # -- request building -------------------------------------------------

    def _payload(self, system: str, user: str, schema: dict[str, Any], *, mode: str) -> dict:
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        if mode == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": schema, "strict": True},
            }
        elif mode == "json_object":
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _post_once(
        self, system: str, user: str, schema: dict[str, Any], *, mode: str
    ) -> httpx.Response:
        payload = self._payload(system, user, schema, mode=mode)
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        client = self.http_client
        if client is not None:
            return client.post(url, json=payload, headers=headers, timeout=self.timeout_s)
        return httpx.post(url, json=payload, headers=headers, timeout=self.timeout_s)

    def _post_with_retry(
        self, system: str, user: str, schema: dict[str, Any], *, mode: str
    ) -> httpx.Response:
        attempts = self.max_transport_retries + 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            retry_after: float | None = None
            try:
                resp = self._post_once(system, user, schema, mode=mode)
            except httpx.TimeoutException as exc:
                last_exc = LLMRetryableError(f"LLM request timed out: {exc}")
            except httpx.HTTPError as exc:
                last_exc = LLMRetryableError(f"LLM connection error: {exc}")
            else:
                if resp.status_code == 429:
                    retry_after = _parse_retry_after(resp)
                    last_exc = LLMQuotaOrRateLimitError(
                        "LLM provider rate limit or quota exceeded (HTTP 429)"
                    )
                elif 500 <= resp.status_code < 600:
                    retry_after = _parse_retry_after(resp)
                    last_exc = LLMRetryableError(f"LLM transient HTTP {resp.status_code}")
                elif resp.status_code in (401, 403):
                    raise LLMAuthenticationError(
                        f"LLM authentication failed (HTTP {resp.status_code}). "
                        "Check SPARKPLAN_LLM_API_KEY."
                    )
                elif resp.status_code >= 400:
                    safe_preview = resp.text[:200]
                    if self.api_key:
                        safe_preview = safe_preview.replace(self.api_key, "[REDACTED]")
                    raise LLMError(f"LLM HTTP {resp.status_code}: {safe_preview}")
                else:
                    return resp
            if attempt == attempts - 1:
                break
            # Bounded backoff: honor the provider's retry-after (capped), else a
            # small exponential wait. Keeps one repair round from dying on a
            # brief rate limit while staying strictly bounded.
            delay = retry_after if retry_after is not None else self.backoff_base_s * (2**attempt)
            time.sleep(min(delay, self.max_backoff_s))
        assert last_exc is not None
        raise last_exc

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> LLMResponse:
        try:
            resp = self._post_with_retry(system, user, schema, mode="json_schema")
        except LLMError as exc:
            # A non-retryable 4xx on the schema mode may simply mean the
            # provider does not implement json_schema; try json_object once.
            if _looks_like_schema_unsupported(exc):
                resp = self._post_with_retry(system, user, schema, mode="json_object")
            else:
                raise

        try:
            body = resp.json()
            choice = body["choices"][0]
            content = choice["message"]["content"]
            data = json.loads(content) if isinstance(content, str) else content
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise LLMError(f"unexpected LLM response shape: {exc}") from exc
        if not isinstance(data, dict):
            raise LLMError(f"LLM returned non-object JSON: {type(data).__name__}")
        reason = choice.get("finish_reason")
        return LLMResponse(
            data=data,
            usage=LLMUsage.from_provider(body.get("usage")),
            finish_reason=reason if isinstance(reason, str) else None,
        )


def _parse_retry_after(resp: httpx.Response) -> float | None:
    """Read a numeric ``Retry-After`` header (seconds) if present."""
    raw = resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _looks_like_schema_unsupported(exc: LLMError) -> bool:
    """True when a schema-mode 400 suggests falling back to json_object.

    Covers both providers that do not implement ``json_schema`` and providers
    (e.g. Groq) that implement it but reject a specific generation with a
    ``json_validate_failed`` error. Either way the looser ``json_object`` mode
    is a reasonable one-shot fallback; the payload is re-validated locally.
    """
    msg = str(exc).lower()
    return any(
        marker in msg
        for marker in (
            "json_schema",
            "response_format",
            "schema",
            "json_validate_failed",
            "failed to validate json",
            "failed_generation",
        )
    )


def load_dotenv(path: Path | str | None = None) -> bool:
    """Load SPARKPLAN_* and other keys from a .env file without overwriting existing env.

    Minimal stdlib parser without external dependencies.
    Existing environment variables are preserved.
    Never logs or echoes any secret values.
    Returns True if a file was found and processed, False otherwise.
    """
    if path is None and ("pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ):
        return False

    if path is not None:
        target = Path(path)
        candidates = [target] if target.is_file() else []
    else:
        candidates = []
        cwd = Path.cwd().resolve()
        for directory in [cwd, *cwd.parents]:
            candidate = directory / ".env"
            if candidate.is_file():
                candidates.append(candidate)
                break
        try:
            source_root_env = Path(__file__).resolve().parents[3] / ".env"
            if source_root_env.is_file() and source_root_env not in candidates:
                candidates.append(source_root_env)
        except IndexError:
            pass

    loaded = False
    for p in candidates:
        if not p.is_file():
            continue
        try:
            content = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw in content.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
        loaded = True
        break
    return loaded


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise LLMConfigurationError(f"{name} must be a number, got {raw!r}") from exc


def _env_int_optional(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise LLMConfigurationError(f"{name} must be an integer, got {raw!r}") from exc


def make_llm_from_env(*, load_env: bool = False) -> LLMClient:
    """Build a client from ``SPARKPLAN_LLM_*`` env vars.

    Defaults to a real OpenAI-compatible client. Unit tests must inject a
    ``FakeLLM`` explicitly; this factory never fabricates one silently.
    """
    if load_env:
        load_dotenv()

    provider = os.environ.get("SPARKPLAN_LLM_PROVIDER", "").strip().lower()
    if provider == "fake":
        return FakeLLM(script=[])
    if provider not in ("", "openai_compat", "openai", "groq"):
        raise LLMConfigurationError(f"unknown SPARKPLAN_LLM_PROVIDER {provider!r}")

    base_url = os.environ.get("SPARKPLAN_LLM_BASE_URL", "").strip()
    api_key = os.environ.get("SPARKPLAN_LLM_API_KEY", "").strip()
    model = os.environ.get("SPARKPLAN_LLM_MODEL", "").strip()

    missing: list[str] = []
    if not base_url:
        missing.append("SPARKPLAN_LLM_BASE_URL")
    if not api_key:
        missing.append("SPARKPLAN_LLM_API_KEY")
    if not model:
        missing.append("SPARKPLAN_LLM_MODEL")

    if missing:
        raise LLMConfigurationError(f"Missing required LLM configuration: {', '.join(missing)}")
    return OpenAICompatibleClient(
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_s=_env_float("SPARKPLAN_LLM_TIMEOUT_S", 60.0),
        max_tokens=_env_int_optional("SPARKPLAN_LLM_MAX_TOKENS"),
        max_transport_retries=_env_int_optional("SPARKPLAN_LLM_MAX_RETRIES") or 1,
    )
