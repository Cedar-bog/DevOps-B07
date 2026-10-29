"""Injectable LLM adapter for the DRAFT service.

The adapter reads an OpenAI-compatible chat-completions configuration from the
process environment (optionally supplemented by a ``.env`` file), sends a
prompt, and returns the model reply without rewriting it.  The HTTP transport
is a callable so tests and the DRAFT pipeline can inject a fake model instead
of making a network request.

Configuration keys (environment variables unless ``env_file`` is given):

``LLM_API_URL``
    Base URL (for example ``https://api.deepseek.com/v1``) or the full
    ``.../chat/completions`` endpoint.
``LLM_API_KEY``
    Bearer token.  It is kept out of ``repr()`` and is redacted from errors.
``LLM_MODEL``
    Model name sent in the request body.
``LLM_TIMEOUT``
    Optional per-request timeout in seconds (default: 120).
``LLM_MAX_TOKENS``
    Optional ``max_tokens`` value.
``LLM_TEMPERATURE``
    Optional sampling temperature.

The API key is never printed by this module.  Callers should not log request
headers or the raw configuration object without redacting it first.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

DEFAULT_TIMEOUT = 120.0
MAX_ERROR_CHARS = 600
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REQUIRED_ENV = ("LLM_API_URL", "LLM_API_KEY", "LLM_MODEL")


class LLMError(Exception):
    """Base class for LLM adapter errors."""


class LLMConfigError(LLMError, ValueError):
    """The LLM configuration is missing or invalid."""


class LLMRequestError(LLMError):
    """The LLM endpoint could not be reached or returned an error status."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        endpoint: str | None = None,
        detail: str = "",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.endpoint = endpoint
        self.detail = detail


class LLMResponseError(LLMError):
    """The endpoint returned a response that is not a usable chat completion."""


@dataclass(frozen=True)
class LLMConfig:
    """Validated settings for one OpenAI-compatible endpoint."""

    api_url: str
    api_key: str = field(repr=False)
    model: str
    timeout: float = DEFAULT_TIMEOUT
    max_tokens: int | None = None
    temperature: float | None = None

    def __post_init__(self) -> None:
        api_url = self.api_url.strip()
        api_key = self.api_key.strip()
        model = self.model.strip()

        if not api_url:
            raise LLMConfigError("LLM_API_URL is empty")
        parsed = urllib.parse.urlsplit(api_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise LLMConfigError(
                "LLM_API_URL must be an absolute http(s) URL, "
                "for example https://api.deepseek.com/v1"
            )
        if parsed.username or parsed.password:
            raise LLMConfigError("LLM_API_URL must not contain credentials")
        if not api_key:
            raise LLMConfigError("LLM_API_KEY is empty")
        if not model:
            raise LLMConfigError("LLM_MODEL is empty")

        try:
            timeout = float(self.timeout)
        except (TypeError, ValueError) as exc:
            raise LLMConfigError("LLM_TIMEOUT must be a number") from exc
        if timeout <= 0:
            raise LLMConfigError("LLM_TIMEOUT must be greater than zero")

        if self.max_tokens is not None:
            if not isinstance(self.max_tokens, int) or isinstance(self.max_tokens, bool):
                raise LLMConfigError("LLM_MAX_TOKENS must be an integer")
            if self.max_tokens <= 0:
                raise LLMConfigError("LLM_MAX_TOKENS must be greater than zero")

        if self.temperature is not None:
            try:
                temperature = float(self.temperature)
            except (TypeError, ValueError) as exc:
                raise LLMConfigError("LLM_TEMPERATURE must be a number") from exc
            if not 0 <= temperature <= 2:
                raise LLMConfigError("LLM_TEMPERATURE must be between 0 and 2")
            object.__setattr__(self, "temperature", temperature)

        object.__setattr__(self, "api_url", api_url)
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "timeout", timeout)

    @property
    def endpoint(self) -> str:
        """The resolved chat-completions URL."""
        return chat_endpoint(self.api_url)


@dataclass(frozen=True)
class HTTPRequest:
    """A minimal HTTP request understood by an injectable transport."""

    method: str
    url: str
    headers: Mapping[str, str] = field(repr=False)
    body: bytes


@dataclass(frozen=True)
class HTTPResponse:
    """The status and body returned by an injectable transport."""

    status: int
    body: bytes


Transport = Callable[[HTTPRequest, float], HTTPResponse]


@dataclass(frozen=True)
class LLMResponse:
    """One model reply.

    ``content`` is the assistant text exactly as returned (no stripping or
    whitespace normalisation); ``raw_response`` keeps the complete response
    body for evidence archives.
    """

    content: str
    model: str
    status: int
    endpoint: str
    finish_reason: str | None = None
    usage: Mapping[str, Any] | None = None
    raw_response: str = ""
    payload: Mapping[str, Any] | None = None
    latency_seconds: float = 0.0

    @property
    def text(self) -> str:
        """Alias for ``content`` used by prompt/result code."""
        return self.content

    def to_payload(self) -> dict[str, Any]:
        """Return a JSON-serialisable record suitable for result evidence."""
        return {
            "content": self.content,
            "model": self.model,
            "status": self.status,
            "endpoint": self.endpoint,
            "finish_reason": self.finish_reason,
            "usage": dict(self.usage) if self.usage is not None else None,
            "latency_seconds": self.latency_seconds,
            "raw_response": self.raw_response,
        }


def chat_endpoint(api_url: str) -> str:
    """Turn a base URL into an OpenAI-compatible chat-completions URL."""
    parsed = urllib.parse.urlsplit(api_url.strip())
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        endpoint_path = path
    elif path.endswith("/v1"):
        endpoint_path = f"{path}/chat/completions"
    elif not path:
        endpoint_path = "/v1/chat/completions"
    else:
        endpoint_path = f"{path}/chat/completions"
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, endpoint_path, parsed.query, "")
    )


def display_endpoint(url: str) -> str:
    """Return a URL without query or fragment, which may contain secrets."""
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "", "")
    )


def parse_env_file(path: Path | str) -> dict[str, str]:
    """Parse a simple ``KEY=VALUE`` file without executing shell code."""
    env_path = Path(path).expanduser()
    values: dict[str, str] = {}
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not ENV_KEY_RE.fullmatch(key):
            continue
        value = value.strip()
        if value[:1] in {"'", '"'}:
            quote = value[0]
            end = value.rfind(quote)
            value = value[1:end] if end > 0 else value[1:]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        values[key] = value
    return values


def load_config(
    *,
    env_file: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> LLMConfig:
    """Load and validate LLM settings.

    Process environment values take precedence over values from ``env_file``.
    When ``env_file`` is omitted only the process environment is consulted.
    ``environ`` is injectable for tests.
    """
    values = dict(os.environ if environ is None else environ)

    if env_file is not None:
        path = Path(env_file).expanduser()
        if not path.is_file():
            raise LLMConfigError(f"env file not found: {path}")
        try:
            file_values = parse_env_file(path)
        except OSError as exc:
            raise LLMConfigError(f"cannot read {path}: {exc}") from exc
        for key, value in file_values.items():
            values.setdefault(key, value)

    def get(name: str) -> str:
        value = values.get(name)
        if value is None:
            return ""
        return value.strip() if isinstance(value, str) else str(value).strip()

    missing = [name for name in _REQUIRED_ENV if not get(name)]
    if missing:
        raise LLMConfigError(
            f"missing {', '.join(missing)}; set the environment variables "
            "or provide an env_file"
        )

    max_tokens = _optional_int("LLM_MAX_TOKENS", get("LLM_MAX_TOKENS"))
    temperature = _optional_float("LLM_TEMPERATURE", get("LLM_TEMPERATURE"))
    timeout = _optional_float("LLM_TIMEOUT", get("LLM_TIMEOUT"))
    return LLMConfig(
        api_url=get("LLM_API_URL"),
        api_key=get("LLM_API_KEY"),
        model=get("LLM_MODEL"),
        timeout=DEFAULT_TIMEOUT if timeout is None else timeout,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def _optional_int(name: str, raw: str) -> int | None:
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise LLMConfigError(f"{name} must be an integer") from exc


def _optional_float(name: str, raw: str) -> float | None:
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise LLMConfigError(f"{name} must be a number") from exc


def urllib_transport(request: HTTPRequest, timeout: float) -> HTTPResponse:
    """Default transport using :mod:`urllib` from the standard library."""
    http_request = urllib.request.Request(
        request.url,
        data=request.body,
        method=request.method,
        headers=dict(request.headers),
    )
    try:
        with urllib.request.urlopen(http_request, timeout=timeout) as response:
            return HTTPResponse(status=response.status, body=response.read())
    except urllib.error.HTTPError as exc:
        return HTTPResponse(status=exc.code, body=exc.read())


def _redact(text: str, secret: str) -> str:
    if secret:
        text = text.replace(secret, "***")
    text = re.sub(r"(?i)(bearer\s+)[^\s\"']+", r"\1***", text)
    return re.sub(
        r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)(?:bearer\s+)?[^\s\"',}]+",
        r"\1***",
        text,
    )


def _error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("type") or error.get("code")
            if message:
                return str(message)
        if isinstance(error, str):
            return error
        message = payload.get("message")
        if message:
            return str(message)
    return ""


def _content_from_payload(payload: Mapping[str, Any]) -> tuple[str, str | None]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMResponseError("response contains no choices")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise LLMResponseError("response choice is not an object")

    content: Any = None
    message = choice.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if content is None or content == "":
            content = message.get("reasoning_content")
    if content is None:
        content = choice.get("text")

    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        content = "".join(parts)

    if not isinstance(content, str):
        raise LLMResponseError("response choice has no text content")

    finish_reason = choice.get("finish_reason")
    if not isinstance(finish_reason, str):
        finish_reason = None
    return content, finish_reason


class LLMClient:
    """Send prompts to an OpenAI-compatible chat endpoint.

    ``transport`` receives an :class:`HTTPRequest` and a timeout and returns an
    :class:`HTTPResponse`.  Tests can pass a fake transport; production code
    omits it and uses :func:`urllib_transport`.
    """

    def __init__(
        self, config: LLMConfig, *, transport: Transport | None = None
    ) -> None:
        self.config = config
        self._transport = transport or urllib_transport

    @classmethod
    def from_env(
        cls,
        *,
        env_file: Path | str | None = None,
        environ: Mapping[str, str] | None = None,
        transport: Transport | None = None,
    ) -> "LLMClient":
        """Build a client from environment variables (and optional ``.env``)."""
        return cls(
            load_config(env_file=env_file, environ=environ),
            transport=transport,
        )

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        timeout: float | None = None,
    ) -> LLMResponse:
        """Send a chat message list and return the unmodified assistant reply."""
        if not messages:
            raise LLMError("messages must not be empty")

        request_timeout = self.config.timeout if timeout is None else float(timeout)
        if request_timeout <= 0:
            raise LLMError("timeout must be greater than zero")

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": list(messages),
            "stream": False,
        }
        if self.config.max_tokens is not None:
            payload["max_tokens"] = self.config.max_tokens
        if self.config.temperature is not None:
            payload["temperature"] = self.config.temperature

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        endpoint = self.config.endpoint
        request = HTTPRequest(
            method="POST",
            url=endpoint,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "devops-b07-draft/1.0",
            },
            body=body,
        )

        started = time.monotonic()
        try:
            response = self._transport(request, request_timeout)
        except LLMError:
            raise
        except TimeoutError as exc:
            raise LLMRequestError(
                f"request timed out after {request_timeout:g}s",
                endpoint=display_endpoint(endpoint),
            ) from exc
        except OSError as exc:
            detail = _redact(str(exc), self.config.api_key)[:MAX_ERROR_CHARS]
            raise LLMRequestError(
                f"request failed: {detail or 'network error'}",
                endpoint=display_endpoint(endpoint),
                detail=detail,
            ) from exc
        latency = time.monotonic() - started

        raw_response = response.body.decode("utf-8", errors="replace")
        try:
            data = json.loads(raw_response) if raw_response else None
        except json.JSONDecodeError:
            data = None

        if not 200 <= response.status < 300:
            detail = _error_message(data) if data is not None else raw_response.strip()
            detail = _redact(detail, self.config.api_key)[:MAX_ERROR_CHARS]
            raise LLMRequestError(
                f"HTTP {response.status}: {detail or 'empty response'}",
                status=response.status,
                endpoint=display_endpoint(endpoint),
                detail=detail,
            )

        if not isinstance(data, dict):
            snippet = _redact(raw_response.strip(), self.config.api_key)[:MAX_ERROR_CHARS]
            raise LLMResponseError(
                f"response is not a JSON object: {snippet or 'empty body'}"
            )

        content, finish_reason = _content_from_payload(data)
        usage = data.get("usage")
        model = data.get("model")
        return LLMResponse(
            content=content,
            model=model if isinstance(model, str) and model else self.config.model,
            status=response.status,
            endpoint=display_endpoint(endpoint),
            finish_reason=finish_reason,
            usage=usage if isinstance(usage, dict) else None,
            raw_response=raw_response,
            payload=data,
            latency_seconds=round(latency, 3),
        )

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        timeout: float | None = None,
    ) -> LLMResponse:
        """Send one user prompt, optionally preceded by a system message."""
        if not isinstance(prompt, str):
            raise LLMError("prompt must be a string")
        messages: list[dict[str, str]] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages, timeout=timeout)
