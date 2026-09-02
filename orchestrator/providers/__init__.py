"""Provider adapter registry — Anthropic, OpenAI, and future transports.

Each adapter wraps a third-party SDK behind the project's ``ChatRequest`` /
``ChatResult`` / ``ChatAdapter`` protocol from ``provider_chat``.

Usage::

    from providers import anthropic_provider, openai_provider
    from provider_chat import register

    register("anthropic", anthropic_provider.AnthropicAdapter())
    register("openai", openai_provider.OpenAIAdapter())
"""

from __future__ import annotations
