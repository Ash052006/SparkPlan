"""Phase 3 LLM client tests. Offline: httpx.MockTransport, no network.

Covers requirement 5 (transport retry semantics) and requirement 6
(structured output primary json_schema mode and json_object fallback), plus
provider usage parsing.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sparkplan.llm.phase3 import (
    FakeLLM,
    LLMError,
    LLMResponse,
    LLMRetryableError,
    LLMUsage,
    OpenAICompatibleClient,
    make_llm_from_env,
)

SCHEMA = {"type": "object", "properties": {"status": {"type": "string"}}}


def _ok_body(content: dict, usage: dict | None = None) -> dict:
    body = {"choices": [{"message": {"content": json.dumps(content)}}]}
    if usage is not None:
        body["usage"] = usage
    return body


def _client(handler, **kw) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        base_url="https://example.invalid/v1",
        api_key="test-key-not-real",
        model="test-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kw,
    )


# ---------------------------------------------------------------------------
# Structured output modes (requirement 6)
# ---------------------------------------------------------------------------


def test_primary_json_schema_mode_is_used():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload.get("response_format", {}).get("type"))
        return httpx.Response(
            200, json=_ok_body({"status": "ok"}, {"prompt_tokens": 5, "completion_tokens": 2})
        )

    resp = _client(handler).complete_json("sys", "user", SCHEMA)
    assert seen == ["json_schema"]
    assert resp.data == {"status": "ok"}
    assert resp.usage == LLMUsage(5, 2, True)


def test_json_object_fallback_on_schema_rejection():
    modes = []

    def handler(request: httpx.Request) -> httpx.Response:
        rf = json.loads(request.content).get("response_format", {})
        modes.append(rf.get("type"))
        if rf.get("type") == "json_schema":
            return httpx.Response(400, text="unknown response_format: json_schema not supported")
        return httpx.Response(200, json=_ok_body({"status": "ok"}))

    resp = _client(handler).complete_json("sys", "user", SCHEMA)
    assert modes == ["json_schema", "json_object"]
    assert resp.data == {"status": "ok"}


def test_non_schema_4xx_is_not_retried_as_fallback():
    modes = []

    def handler(request: httpx.Request) -> httpx.Response:
        modes.append(json.loads(request.content).get("response_format", {}).get("type"))
        return httpx.Response(401, text="invalid api key")

    with pytest.raises(LLMError):
        _client(handler).complete_json("sys", "user", SCHEMA)
    assert modes == ["json_schema"]


# ---------------------------------------------------------------------------
# Transport retries (requirement 5), separate from planner repair rounds
# ---------------------------------------------------------------------------


def test_transient_5xx_retried_once_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json=_ok_body({"status": "ok"}))

    resp = _client(handler, max_transport_retries=1).complete_json("sys", "user", SCHEMA)
    assert resp.data == {"status": "ok"}
    assert calls["n"] == 2


def test_transient_failure_exhausts_retries_and_raises():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text="down")

    with pytest.raises(LLMRetryableError):
        _client(handler, max_transport_retries=1).complete_json("sys", "user", SCHEMA)
    assert calls["n"] == 2  # 1 initial + 1 retry, never unbounded


def test_timeout_is_retryable():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.TimeoutException("too slow")

    with pytest.raises(LLMRetryableError):
        _client(handler, max_transport_retries=1).complete_json("sys", "user", SCHEMA)
    assert calls["n"] == 2


def test_429_is_retryable():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, json=_ok_body({"status": "ok"}))

    resp = _client(handler).complete_json("sys", "user", SCHEMA)
    assert resp.usage.present is False
    assert calls["n"] == 2


def test_parse_retry_after_valid_and_invalid():
    from sparkplan.llm.phase3.client import _parse_retry_after

    assert _parse_retry_after(httpx.Response(429, headers={"retry-after": "2.5"})) == 2.5
    assert _parse_retry_after(httpx.Response(429, headers={"retry-after": "bad"})) is None
    assert _parse_retry_after(httpx.Response(429)) is None


def test_429_honors_retry_after_header():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, text="slow down")
        return httpx.Response(200, json=_ok_body({"status": "ok"}))

    resp = _client(handler, backoff_base_s=0.0, max_backoff_s=0.0).complete_json(
        "sys", "user", SCHEMA
    )
    assert resp.data == {"status": "ok"}
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# Response-shape and usage parsing
# ---------------------------------------------------------------------------


def test_usage_absent_is_unknown():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_body({"status": "ok"}))

    resp = _client(handler).complete_json("sys", "user", SCHEMA)
    assert resp.usage.present is False


def test_usage_null_is_unknown():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_ok_body({"status": "ok"}, {"prompt_tokens": None, "completion_tokens": None})
        )

    resp = _client(handler).complete_json("sys", "user", SCHEMA)
    assert resp.usage.present is False


def test_malformed_body_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    with pytest.raises(LLMError):
        _client(handler).complete_json("sys", "user", SCHEMA)


def test_non_object_json_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "[1,2,3]"}}]})

    with pytest.raises(LLMError):
        _client(handler).complete_json("sys", "user", SCHEMA)


# ---------------------------------------------------------------------------
# FakeLLM contract
# ---------------------------------------------------------------------------


def test_fakellm_records_calls_and_replays_last():
    llm = FakeLLM(script=[{"a": 1}])
    for _ in range(3):
        assert llm.complete_json("s", "u", SCHEMA).data == {"a": 1}
    assert len(llm.calls) == 3
    assert llm.calls[0]["system"] == "s"


def test_fakellm_raises_scripted_exception():
    llm = FakeLLM(script=[LLMRetryableError("x")])
    with pytest.raises(LLMRetryableError):
        llm.complete_json("s", "u", SCHEMA)


def test_fakellm_empty_script_raises():
    with pytest.raises(LLMError):
        FakeLLM(script=[]).complete_json("s", "u", SCHEMA)


def test_llm_response_defaults_to_unknown_usage():
    assert LLMResponse(data={}).usage.present is False


def test_llm_response_truncated_flag():
    assert LLMResponse(data={}, finish_reason="length").truncated is True
    assert LLMResponse(data={}, finish_reason="stop").truncated is False
    assert LLMResponse(data={}).truncated is False


def test_finish_reason_is_captured():
    def handler(request: httpx.Request) -> httpx.Response:
        body = _ok_body({"status": "ok"})
        body["choices"][0]["finish_reason"] = "length"
        return httpx.Response(200, json=body)

    resp = _client(handler).complete_json("sys", "user", SCHEMA)
    assert resp.finish_reason == "length"
    assert resp.truncated is True


def test_finish_reason_absent_is_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_body({"status": "ok"}))

    resp = _client(handler).complete_json("sys", "user", SCHEMA)
    assert resp.finish_reason is None
    assert resp.truncated is False


def test_fakellm_returns_finish_reason():
    llm = FakeLLM(script=[({"a": 1}, None, "length")])
    resp = llm.complete_json("s", "u", SCHEMA)
    assert resp.data == {"a": 1}
    assert resp.finish_reason == "length"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_make_llm_from_env_requires_config(monkeypatch):
    for key in (
        "SPARKPLAN_LLM_PROVIDER",
        "SPARKPLAN_LLM_BASE_URL",
        "SPARKPLAN_LLM_API_KEY",
        "SPARKPLAN_LLM_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(LLMError):
        make_llm_from_env()


def test_make_llm_from_env_fake_provider(monkeypatch):
    monkeypatch.setenv("SPARKPLAN_LLM_PROVIDER", "fake")
    assert isinstance(make_llm_from_env(), FakeLLM)


def test_make_llm_from_env_builds_client(monkeypatch):
    monkeypatch.setenv("SPARKPLAN_LLM_PROVIDER", "openai_compat")
    monkeypatch.setenv("SPARKPLAN_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("SPARKPLAN_LLM_API_KEY", "test-key-not-real")
    monkeypatch.setenv("SPARKPLAN_LLM_MODEL", "test-model")
    monkeypatch.setenv("SPARKPLAN_LLM_TIMEOUT_S", "30")
    monkeypatch.setenv("SPARKPLAN_LLM_MAX_TOKENS", "1024")
    client = make_llm_from_env()
    assert isinstance(client, OpenAICompatibleClient)
    assert client.timeout_s == 30
    assert client.max_tokens == 1024


def test_make_llm_from_env_bad_timeout(monkeypatch):
    monkeypatch.setenv("SPARKPLAN_LLM_PROVIDER", "openai_compat")
    monkeypatch.setenv("SPARKPLAN_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("SPARKPLAN_LLM_API_KEY", "test-key-not-real")
    monkeypatch.setenv("SPARKPLAN_LLM_MODEL", "test-model")
    monkeypatch.setenv("SPARKPLAN_LLM_TIMEOUT_S", "not-a-number")
    with pytest.raises(LLMError):
        make_llm_from_env()


def test_make_llm_from_env_defaults_and_blank_optionals(monkeypatch):
    monkeypatch.setenv("SPARKPLAN_LLM_PROVIDER", "openai_compat")
    monkeypatch.setenv("SPARKPLAN_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("SPARKPLAN_LLM_API_KEY", "test-key-not-real")
    monkeypatch.setenv("SPARKPLAN_LLM_MODEL", "test-model")
    monkeypatch.delenv("SPARKPLAN_LLM_TIMEOUT_S", raising=False)
    monkeypatch.setenv("SPARKPLAN_LLM_MAX_TOKENS", "")
    client = make_llm_from_env()
    assert client.timeout_s == 60.0
    assert client.max_tokens is None


def test_make_llm_from_env_bad_max_tokens(monkeypatch):
    monkeypatch.setenv("SPARKPLAN_LLM_PROVIDER", "openai_compat")
    monkeypatch.setenv("SPARKPLAN_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("SPARKPLAN_LLM_API_KEY", "test-key-not-real")
    monkeypatch.setenv("SPARKPLAN_LLM_MODEL", "test-model")
    monkeypatch.setenv("SPARKPLAN_LLM_MAX_TOKENS", "many")
    with pytest.raises(LLMError):
        make_llm_from_env()


def test_make_llm_from_env_unknown_provider(monkeypatch):
    monkeypatch.setenv("SPARKPLAN_LLM_PROVIDER", "totally-made-up")
    with pytest.raises(LLMError):
        make_llm_from_env()


def test_max_tokens_included_in_payload_when_set():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content).get("max_tokens"))
        return httpx.Response(200, json=_ok_body({"status": "ok"}))

    _client(handler, max_tokens=512).complete_json("sys", "user", SCHEMA)
    assert seen == [512]


def test_connection_error_is_retryable():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMRetryableError):
        _client(handler, max_transport_retries=1).complete_json("sys", "user", SCHEMA)
    assert calls["n"] == 2
