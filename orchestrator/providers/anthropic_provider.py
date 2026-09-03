"""``ChatAdapter`` for Anthropic's Messages API.

Wraps the ``anthropic`` SDK (version 0.87.x).  The SDK must be importable
(``pip install anthropic``) at runtime; this module never imports it at
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
    _secure_env_value,
)


def _build_client(*, api_key: str | None = None):
    """Lazy import and construct.  Raises ``ImportError`` if the SDK is
    missing, which is caught by the caller and surfaced as an
    ``UNSUPPORTED_PROVIDER`` error."""
    import anthropic  # type: ignore[import-untyped]  # noqa: F811

    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise ProviderChatError(
            "Anthropic API key is not configured",
            ErrorCategory.AUTHENTICATION,
        )
    return anthropic.Anthropic(api_key=key)


class AnthropicAdapter(ChatAdapter):
    """Adapter for Anthropic's Messages API.

    Maps ``ChatRequest`` fields to the SDK's ``messages.create`` call.
    Supports ``claude-sonnet-5``, ``claude-fable-5``, ``claude-opus-5``,
    ``claude-haiku-4-5-20251001`` and any other model the API accepts.
    """

    def chat(self, request: ChatRequest) -> ChatResult:
        if request.provider not in ("anthropic",):
            raise ProviderChatError(
                f"Anthropic adapter received unexpected provider {request.provider!r}",
                ErrorCategory.UNSUPPORTED_PROVIDER,
            )

        try:
            client = _build_client(api_key=_secure_env_value("ANTHROPIC_API_KEY"))
        except ImportError:
            raise ProviderChatError(
                "anthropic SDK is not installed",
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
            "max_tokens": request.response_token_reserve or 4096,
        }
        if request.timeout_seconds:
            kwargs["timeout"] = request.timeout_seconds

        started = time.perf_counter()
        try:
            response = client.messages.create(**kwargs)
        except Exception as exc:
            exc_str = str(exc).lower()
            if "rate" in exc_str or "429" in exc_str or "too many requests" in exc_str:
                raise ProviderChatError(str(exc), ErrorCategory.RATE_LIMIT, retryable=True) from exc
            if "authentication" in exc_str or "unauthorized" in exc_str or "401" in exc_str:
                raise ProviderChatError(str(exc), ErrorCategory.AUTHENTICATION) from exc
            if "permission" in exc_str or "403" in exc_str:
                raise ProviderChatError(str(exc), ErrorCategory.AUTHORIZATION) from exc
            if "timeout" in exc_str or "timed out" in exc_str:
                raise ProviderChatError(str(exc), ErrorCategory.TIMEOUT, retryable=True) from exc
            if "overloaded" in exc_str or "529" in exc_str:
                raise ProviderChatError(str(exc), ErrorCategory.PROVIDER_SERVER, retryable=True) from exc
            raise ProviderChatError(str(exc), ErrorCategory.TRANSPORT, retryable=True) from exc

        latency = time.perf_counter() - started

        content = ""
        reasoning = ""
        for block in (getattr(response, "content", None) or []):
            if hasattr(block, "type") and block.type == "text":
                content += getattr(block, "text", "") or ""
            elif hasattr(block, "type") and block.type == "thinking":
                reasoning += getattr(block, "thinking", "") or ""

        usage = getattr(response, "usage", None) or {}
        return ChatResult(
            content=content.strip(),
            reasoning=reasoning.strip(),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            finish_reason=getattr(response, "stop_reason", None),
            request_id=getattr(response, "id", None),
            latency_seconds=latency,
            provider=request.provider,
            model=request.model,
        )
