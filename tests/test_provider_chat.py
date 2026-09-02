"""Provider adapter contract tests — all mocked, no live provider calls.

Verifies that Anthropic and OpenAI adapters:
- Map ChatRequest fields correctly
- Handle success responses
- Classify SDK errors into the correct ErrorCategory
- Fail gracefully when the SDK is missing

Strategy: inject mock SDK modules into ``sys.modules`` BEFORE importing the
adapters, so their lazy ``import anthropic`` / ``import openai`` resolves to
the mocks.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orchestrator"))

from provider_chat import (  # noqa: E402
    ChatRequest,
    ChatResult,
    ErrorCategory,
    ProviderChatError,
)

checks = 0
failures: list[str] = []


def check(label: str, got, want=True) -> None:
    global checks
    checks += 1
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  [FAIL] {label}")
    else:
        print(f"  [PASS] {label}")


# ── Test: Anthropic adapter ──────────────────────────────────────────────────


print("=== Anthropic adapter ===")

# Inject mock anthropic SDK before the adapter module is imported.
mock_anthropic = MagicMock()
mock_anthropic_client = MagicMock()
mock_anthropic.Anthropic.return_value = mock_anthropic_client

sys.modules["anthropic"] = mock_anthropic

with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=False):
    from providers.anthropic_provider import AnthropicAdapter  # noqa: E402

    adapter = AnthropicAdapter()
    client = mock_anthropic_client

    # Success response
    msg = MagicMock()
    msg.content = [
        MagicMock(type="text", text="Hello from Claude!"),
    ]
    msg.usage = MagicMock()
    msg.usage.input_tokens = 15
    msg.usage.output_tokens = 42
    msg.stop_reason = "end_turn"
    msg.id = "msg_abc123"
    client.messages.create.return_value = msg

    req = ChatRequest(
        provider="anthropic",
        model="claude-sonnet-5",
        prompt="Say hello",
        response_token_reserve=100,
    )
    result = adapter.chat(req)
    check("anthropic: content matches", result.content, "Hello from Claude!")
    check("anthropic: input_tokens", result.input_tokens, 15)
    check("anthropic: output_tokens", result.output_tokens, 42)
    check("anthropic: finish_reason", result.finish_reason, "end_turn")
    check("anthropic: request_id", result.request_id, "msg_abc123")
    check("anthropic: provider", result.provider, "anthropic")
    check("anthropic: model", result.model, "claude-sonnet-5")
    check("anthropic: latency > 0", result.latency_seconds > 0)

    # Thinking + content blocks
    msg2 = MagicMock()
    msg2.content = [
        MagicMock(type="thinking", thinking="I'm thinking..."),
        MagicMock(type="text", text="Final answer."),
    ]
    msg2.usage = MagicMock()
    msg2.usage.input_tokens = 0
    msg2.usage.output_tokens = 0
    msg2.stop_reason = "end_turn"
    msg2.id = "msg_think"
    client.messages.create.return_value = msg2

    result2 = adapter.chat(ChatRequest(provider="anthropic", model="claude-sonnet-5", prompt="think"))
    check("anthropic: extracts reasoning", result2.reasoning, "I'm thinking...")
    check("anthropic: extracts content after thinking", result2.content, "Final answer.")

    # Rate limit error
    client.messages.create.side_effect = RuntimeError("429 too many requests")
    try:
        adapter.chat(ChatRequest(provider="anthropic", model="claude-sonnet-5", prompt="x"))
        check("anthropic: rate limit raised", False)
    except ProviderChatError as e:
        check("anthropic: rate limit category", e.category, ErrorCategory.RATE_LIMIT)
        check("anthropic: rate limit retryable", e.retryable, True)
    client.messages.create.side_effect = None

    # Auth error
    client.messages.create.side_effect = RuntimeError("authentication failed: 401")
    try:
        adapter.chat(ChatRequest(provider="anthropic", model="claude-sonnet-5", prompt="x"))
        check("anthropic: auth raised", False)
    except ProviderChatError as e:
        check("anthropic: auth category", e.category, ErrorCategory.AUTHENTICATION)
    client.messages.create.side_effect = None

    # Wrong provider
    try:
        adapter.chat(ChatRequest(provider="openai", model="gpt-4o", prompt="x"))
        check("anthropic: wrong provider raised", False)
    except ProviderChatError as e:
        check("anthropic: wrong provider category", e.category, ErrorCategory.UNSUPPORTED_PROVIDER)


# ── Test: OpenAI adapter ─────────────────────────────────────────────────────


print("\n=== OpenAI adapter ===")

# Inject mock openai SDK
mock_openai = MagicMock()
mock_openai_client = MagicMock()
mock_openai.OpenAI.return_value = mock_openai_client

sys.modules["openai"] = mock_openai

with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-openai-test"}, clear=False):
    from providers.openai_provider import OpenAIAdapter  # noqa: E402

    adapter = OpenAIAdapter()
    client = mock_openai_client

    # Success response
    completion = MagicMock()
    choice = MagicMock()
    msg = MagicMock()
    msg.content = "Hello from GPT!"
    msg.reasoning_content = ""
    choice.message = msg
    choice.finish_reason = "stop"
    completion.choices = [choice]
    completion.usage = MagicMock()
    completion.usage.prompt_tokens = 10
    completion.usage.completion_tokens = 20
    completion.id = "chatcmpl-xyz"
    client.chat.completions.create.return_value = completion

    req = ChatRequest(
        provider="openai",
        model="gpt-4o",
        prompt="Say hello",
        response_token_reserve=200,
    )
    result = adapter.chat(req)
    check("openai: content matches", result.content, "Hello from GPT!")
    check("openai: input_tokens", result.input_tokens, 10)
    check("openai: output_tokens", result.output_tokens, 20)
    check("openai: finish_reason", result.finish_reason, "stop")
    check("openai: request_id", result.request_id, "chatcmpl-xyz")
    check("openai: provider", result.provider, "openai")
    check("openai: model", result.model, "gpt-4o")
    check("openai: latency > 0", result.latency_seconds > 0)

    # Reasoning content
    completion2 = MagicMock()
    choice2 = MagicMock()
    msg2 = MagicMock()
    msg2.content = "Final."
    msg2.reasoning_content = "Step-by-step reasoning..."
    choice2.message = msg2
    choice2.finish_reason = "stop"
    completion2.choices = [choice2]
    completion2.usage = MagicMock()
    completion2.usage.prompt_tokens = 0
    completion2.usage.completion_tokens = 0
    completion2.id = "chatcmpl-reason"
    client.chat.completions.create.return_value = completion2

    result2 = adapter.chat(ChatRequest(provider="openai", model="gpt-4o", prompt="reason"))
    check("openai: extracts reasoning", result2.reasoning, "Step-by-step reasoning...")

    # Rate limit error
    client.chat.completions.create.side_effect = RuntimeError("429 too many requests")
    try:
        adapter.chat(ChatRequest(provider="openai", model="gpt-4o", prompt="x"))
        check("openai: rate limit raised", False)
    except ProviderChatError as e:
        check("openai: rate limit category", e.category, ErrorCategory.RATE_LIMIT)
        check("openai: rate limit retryable", e.retryable, True)
    client.chat.completions.create.side_effect = None

    # Auth error
    client.chat.completions.create.side_effect = RuntimeError("Incorrect API key provided: 401")
    try:
        adapter.chat(ChatRequest(provider="openai", model="gpt-4o", prompt="x"))
        check("openai: auth raised", False)
    except ProviderChatError as e:
        check("openai: auth category", e.category, ErrorCategory.AUTHENTICATION)
    client.chat.completions.create.side_effect = None

    # Wrong provider
    try:
        adapter.chat(ChatRequest(provider="anthropic", model="claude-sonnet-5", prompt="x"))
        check("openai: wrong provider raised", False)
    except ProviderChatError as e:
        check("openai: wrong provider category", e.category, ErrorCategory.UNSUPPORTED_PROVIDER)


# ── Clean up injected modules ────────────────────────────────────────────────

sys.modules.pop("anthropic", None)
sys.modules.pop("openai", None)

# ── Summary ──────────────────────────────────────────────────────────────────

print(f"\n{checks - len(failures)}/{checks} checks passed")
if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    raise SystemExit(1)
