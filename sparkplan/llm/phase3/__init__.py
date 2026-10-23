"""Phase 3 LLM clients: JSON completion with usage accounting."""

from sparkplan.llm.phase3.client import (
    UNKNOWN_USAGE,
    FakeLLM,
    LLMAuthenticationError,
    LLMClient,
    LLMConfigurationError,
    LLMError,
    LLMQuotaOrRateLimitError,
    LLMResponse,
    LLMRetryableError,
    LLMUsage,
    OpenAICompatibleClient,
    load_dotenv,
    make_llm_from_env,
)

__all__ = [
    "UNKNOWN_USAGE",
    "FakeLLM",
    "LLMAuthenticationError",
    "LLMClient",
    "LLMConfigurationError",
    "LLMError",
    "LLMQuotaOrRateLimitError",
    "LLMResponse",
    "LLMRetryableError",
    "LLMUsage",
    "OpenAICompatibleClient",
    "load_dotenv",
    "make_llm_from_env",
]
