"""``ChatAdapter`` for OpenAI's Chat Completions API.

Wraps the ``openai`` SDK (version 2.24.x).  The SDK must be importable
(``pip install openai``) at runtime; this module never imports it at
module level so that a missing package does not break imports of other
providers.
"""
from __future__ import annotations

import os
import time
from typing import Any

from provider_chat import (
    ChatAdapter,
    ChatRequest,
    ChatResult,
    ErrorCategory,
    ProviderChatError,
)


def _build_client(*, api_key: str | None = None):
    """Lazy import and construct.  Raises ``ImportError`` if the SDK is
    missing, which is caught by the caller and surfaced as an
    ``UNSUPPORTED_PROVIDER`` error."""
    import openai  # type: ignore[import-untyped]  # noqa: F811

    key = api_key or os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise ProviderChatError(
            "OpenAI API key is not configured",
            ErrorCategory.AUTHENTICATION,
        )
    return openai.OpenAI(api_key=key)


class OpenAIAdapter(ChatAdapter):
    """Adapter for OpenAI's Chat Completions API.

    Maps ``ChatRequest`` fields to the SDK's ``chat.completions.create`` call.
    Supports ``gpt-4o``, ``gpt-4o-mini``, ``o1``, ``o3``, and any other model
    the API accepts.
    """

    def chat(self, request: ChatRequest) -> ChatResult:
        if request.provider not in ("openai",):
            raise ProviderChatError(
                f"OpenAI adapter received unexpected provider {request.provider!r}",
                ErrorCategory.UNSUPPORTED_PROVIDER,
            )

        try:
            client = _build_client(api_key=os.environ.get("OPENAI_API_KEY"))
        except ImportError:
            raise ProviderChatError(
                "openai SDK is not installed",
                ErrorCategory.UNSUPPORTED_PROVIDER,
            ) from None
        except ProviderChatError:
            raise

        messages = (
            list(request.messages)
            if request.messages
            else [{"role": "user", "content": request.prompt}]
        )

        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
        }
        if request.response_token_reserve is not None:
            kwargs["max_completion_tokens"] = request.response_token_reserve
        if request.timeout_seconds:
            kwargs["timeout"] = request.timeout_seconds

        started = time.perf_counter()
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:
            exc_str = str(exc).lower()
            if "rate" in exc_str or "429" in exc_str or "too many" in exc_str:
                raise ProviderChatError(str(exc), ErrorCategory.RATE_LIMIT, retryable=True) from exc
            if "authentication" in exc_str or "unauthorized" in exc_str or "401" in exc_str:
                raise ProviderChatError(str(exc), ErrorCategory.AUTHENTICATION) from exc
            if "permission" in exc_str or "403" in exc_str:
                raise ProviderChatError(str(exc), ErrorCategory.AUTHORIZATION) from exc
            if "timeout" in exc_str or "timed out" in exc_str:
                raise ProviderChatError(str(exc), ErrorCategory.TIMEOUT, retryable=True) from exc
            if "overloaded" in exc_str or "502" in exc_str or "503" in exc_str:
                raise ProviderChatError(str(exc), ErrorCategory.PROVIDER_SERVER, retryable=True) from exc
            raise ProviderChatError(str(exc), ErrorCategory.TRANSPORT, retryable=True) from exc

        latency = time.perf_counter() - started

        choice = response.choices[0] if response.choices else None
        content = getattr(choice, "message", None)
        content_text = getattr(content, "content", "") or "" if content else ""
        reasoning = getattr(content, "reasoning_content", "") or "" if content else ""
        finish_reason = getattr(choice, "finish_reason", None) if choice else None

        usage = getattr(response, "usage", None) or {}
        return ChatResult(
            content=content_text.strip(),
            reasoning=reasoning.strip(),
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            finish_reason=finish_reason,
            request_id=getattr(response, "id", None),
            latency_seconds=latency,
            provider=request.provider,
            model=request.model,
        )
