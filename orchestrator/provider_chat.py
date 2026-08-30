"""Provider-neutral boundary for tool-free model calls.

Ollama and BytePlus Coding are registered behind one typed contract; unknown
providers fail loudly.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

from dotenv import dotenv_values
from execution_pause import estop_path, pause_engaged

PROVIDER_OPTION_KEYS = (
    "endpoint", "authentication_reference", "context_tokens",
    "response_token_reserve",
)


def options_from_config(config: Mapping[str, Any], purpose: str) -> dict[str, Any]:
    """Return canonical tool-free dispatch kwargs for a model config."""
    if config.get("provider", "ollama") == "ollama":
        return {}
    options = {"provider": config["provider"], "purpose": purpose}
    options.update({key: config[key] for key in PROVIDER_OPTION_KEYS
                    if config.get(key) is not None})
    return options


def _secure_env_value(name: str) -> str:
    """Read a secret from the process or Hermes's canonical private .env."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    try:
        loaded = dotenv_values(estop_path().parent / ".env")
        return str(loaded.get(name) or "").strip()
    except (OSError, ValueError, RuntimeError):
        return ""


def authentication_env_from_config(config: Mapping[str, Any]) -> dict[str, str]:
    """Resolve one declared env reference for a child process without logging it."""
    reference = str(config.get("authentication_reference") or "")
    match = re.fullmatch(r"env:([A-Z][A-Z0-9_]*)", reference)
    if not match:
        return {}
    value = _secure_env_value(match.group(1))
    return {match.group(1): value} if value else {}


class ErrorCategory(str, Enum):
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    QUOTA = "quota"
    RATE_LIMIT = "rate_limit"
    CONTEXT_OVERFLOW = "context_overflow"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    PROVIDER_SERVER = "provider_server"
    EMPTY_RESPONSE = "empty_response"
    MALFORMED_RESPONSE = "malformed_response"
    PAUSED = "paused"
    UNSUPPORTED_PROVIDER = "unsupported_provider"


@dataclass(frozen=True)
class ChatRequest:
    provider: str
    model: str
    prompt: str
    timeout_seconds: float = 300
    endpoint: str | None = None
    messages: tuple[Mapping[str, str], ...] = ()
    context_tokens: int | None = None
    response_token_reserve: int | None = None
    authentication_reference: str | None = None
    purpose: str = "unspecified"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("provider and model are required")
        if not self.prompt and not self.messages:
            raise ValueError("prompt or messages are required")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        for value, name in ((self.context_tokens, "context_tokens"),
                            (self.response_token_reserve, "response_token_reserve")):
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True)
class ChatResult:
    content: str
    reasoning: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str | None = None
    request_id: str | None = None
    latency_seconds: float = 0.0
    error_category: ErrorCategory | None = None
    retryable: bool = False
    provider: str | None = None
    model: str | None = None

    @property
    def usage(self) -> dict[str, int]:
        return {"input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens}


class ChatAdapter(Protocol):
    def chat(self, request: ChatRequest) -> ChatResult: ...


class ProviderChatError(RuntimeError):
    def __init__(self, message: str, category: ErrorCategory, retryable: bool = False):
        super().__init__(message)
        self.category = category
        self.retryable = retryable


class ExecutionPaused(ProviderChatError):
    def __init__(self):
        super().__init__("model execution refused: global ESTOP is engaged",
                         ErrorCategory.PAUSED, retryable=False)


class UnsupportedProvider(ProviderChatError):
    def __init__(self, provider: str):
        super().__init__(f"no chat adapter registered for {provider!r}",
                         ErrorCategory.UNSUPPORTED_PROVIDER, retryable=False)


class _SinglePausedCanaryPermit:
    """Opaque, in-memory authority for exactly one named canary dispatch."""

    def __init__(self, provider: str):
        self._provider = provider
        self._purpose = "connectivity_canary"
        self._remaining = 1
        self._lock = threading.Lock()

    def consume(self, request: ChatRequest) -> None:
        with self._lock:
            if self._remaining != 1:
                raise ExecutionPaused()
            if request.provider != self._provider or request.purpose != self._purpose:
                raise ExecutionPaused()
            # Consume before adapter lookup or invocation. A failed request cannot retry.
            self._remaining = 0


def authorize_single_paused_canary(provider: str) -> _SinglePausedCanaryPermit:
    """Issue one process-local permit; never changes ESTOP or persistent state."""
    if not provider.strip():
        raise ValueError("provider is required")
    return _SinglePausedCanaryPermit(provider)


class OllamaAdapter:
    endpoint = "http://127.0.0.1:11434/api/chat"

    def chat(self, request: ChatRequest) -> ChatResult:
        if request.endpoint is not None and request.endpoint != self.endpoint:
            raise ValueError("Ollama endpoint override is not registered")
        messages = (list(request.messages) if request.messages else
                    [{"role": "user", "content": request.prompt}])
        body = json.dumps({
            "model": request.model,
            "messages": messages,
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            self.endpoint, data=body, headers={"Content-Type": "application/json"})
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=request.timeout_seconds) as response:
                payload = json.loads(response.read())
                request_id = response.headers.get("X-Request-Id")
        except TimeoutError as exc:
            raise ProviderChatError(str(exc), ErrorCategory.TIMEOUT, retryable=True) from exc
        except urllib.error.HTTPError as exc:
            category = (ErrorCategory.RATE_LIMIT if exc.code == 429 else
                        ErrorCategory.AUTHENTICATION if exc.code == 401 else
                        ErrorCategory.AUTHORIZATION if exc.code == 403 else
                        ErrorCategory.PROVIDER_SERVER if exc.code >= 500 else
                        ErrorCategory.TRANSPORT)
            raise ProviderChatError(f"Ollama HTTP {exc.code}", category,
                                    retryable=exc.code == 429 or exc.code >= 500) from exc
        except urllib.error.URLError as exc:
            raise ProviderChatError(str(exc.reason), ErrorCategory.TRANSPORT,
                                    retryable=True) from exc
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
            raise ProviderChatError("Ollama returned a malformed response",
                                    ErrorCategory.MALFORMED_RESPONSE) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("message"), dict):
            raise ProviderChatError("Ollama returned a malformed response",
                                    ErrorCategory.MALFORMED_RESPONSE)
        message = payload.get("message", {})
        content = message.get("content", "")
        if not isinstance(content, str):
            raise ProviderChatError("Ollama returned non-text content",
                                    ErrorCategory.MALFORMED_RESPONSE)
        if not content.strip():
            raise ProviderChatError("Ollama returned an empty response",
                                    ErrorCategory.EMPTY_RESPONSE)
        return ChatResult(
            content=content,
            reasoning=(message.get("thinking") or "").strip(),
            input_tokens=int(payload.get("prompt_eval_count") or 0),
            output_tokens=int(payload.get("eval_count") or 0),
            finish_reason=payload.get("done_reason"), request_id=request_id,
            latency_seconds=time.perf_counter() - started,
            provider=request.provider, model=request.model,
        )


class BytePlusCodingAdapter:
    """OpenAI-compatible ModelArk Coding Plan transport.

    DeepSeek selection is controlled by the Coding Plan console, so callers use
    ``ark-code-latest`` rather than inventing a DeepSeek model identifier.
    """
    base_endpoint = "https://ark.ap-southeast.bytepluses.com/api/coding/v3"

    @staticmethod
    def _api_key(reference: str | None) -> str:
        match = re.fullmatch(r"env:([A-Z][A-Z0-9_]*)",
                             reference or "env:ARK_API_KEY")
        if not match:
            raise ProviderChatError("BytePlus requires an env:NAME authentication reference",
                                    ErrorCategory.AUTHENTICATION)
        value = _secure_env_value(match.group(1))
        if not value:
            raise ProviderChatError("referenced BytePlus API key is unavailable",
                                    ErrorCategory.AUTHENTICATION)
        return value

    def chat(self, request: ChatRequest) -> ChatResult:
        base = (request.endpoint or self.base_endpoint).rstrip("/")
        if base != self.base_endpoint:
            raise ProviderChatError("unregistered BytePlus Coding endpoint",
                                    ErrorCategory.TRANSPORT)
        messages = (list(request.messages) if request.messages else
                    [{"role": "user", "content": request.prompt}])
        body: dict[str, Any] = {"model": request.model, "messages": messages,
                               "stream": False}
        if request.response_token_reserve is not None:
            body["max_completion_tokens"] = request.response_token_reserve
        req = urllib.request.Request(
            f"{base}/chat/completions", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self._api_key(request.authentication_reference)}"})
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=request.timeout_seconds) as response:
                payload = json.loads(response.read())
                request_id = response.headers.get("X-Request-Id")
        except TimeoutError as exc:
            raise ProviderChatError(str(exc), ErrorCategory.TIMEOUT, retryable=True) from exc
        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", errors="replace").strip()
            except Exception:
                pass
            category = (ErrorCategory.RATE_LIMIT if exc.code == 429 else
                        ErrorCategory.AUTHENTICATION if exc.code == 401 else
                        ErrorCategory.AUTHORIZATION if exc.code == 403 else
                        ErrorCategory.PROVIDER_SERVER if exc.code >= 500 else
                        ErrorCategory.TRANSPORT)
            msg = f"BytePlus HTTP {exc.code}" + (f": {err_body}" if err_body else "")
            raise ProviderChatError(msg, category,
                                    retryable=exc.code == 429 or exc.code >= 500) from exc
        except urllib.error.URLError as exc:
            raise ProviderChatError(str(exc.reason), ErrorCategory.TRANSPORT,
                                    retryable=True) from exc
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
            raise ProviderChatError("BytePlus returned a malformed response",
                                    ErrorCategory.MALFORMED_RESPONSE) from exc
        try:
            choice = payload["choices"][0]
            message = choice["message"]
            content = message["content"]
            usage = payload.get("usage") or {}
            if not isinstance(content, str):
                raise TypeError("content is not text")
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderChatError("BytePlus returned a malformed response",
                                    ErrorCategory.MALFORMED_RESPONSE) from exc
        if not content.strip():
            raise ProviderChatError("BytePlus returned an empty response",
                                    ErrorCategory.EMPTY_RESPONSE)
        return ChatResult(
            content=content, reasoning=(message.get("reasoning_content") or "").strip(),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            finish_reason=choice.get("finish_reason"), request_id=request_id,
            latency_seconds=time.perf_counter() - started,
            provider=request.provider, model=request.model)


_ADAPTERS: dict[str, ChatAdapter] = {
    "ollama": OllamaAdapter(),
    "byteplus_coding": BytePlusCodingAdapter(),
}


def register(provider: str, adapter: ChatAdapter) -> None:
    _ADAPTERS[provider] = adapter


def chat(request: ChatRequest, *,
         pause_bypass: _SinglePausedCanaryPermit | None = None) -> ChatResult:
    # This is the single tool-free invocation boundary. Check immediately before
    # adapter lookup/dispatch so every present and future provider fails closed.
    if pause_bypass is not None:
        pause_bypass.consume(request)
    if pause_engaged() and pause_bypass is None:
        raise ExecutionPaused()
    try:
        adapter = _ADAPTERS[request.provider]
    except KeyError:
        raise UnsupportedProvider(request.provider) from None
    return adapter.chat(request)
