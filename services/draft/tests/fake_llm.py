"""Deterministic fake LLM used by the DRAFT tests.

The fake has no network dependency and can be injected in two ways:

* as a stand-in for :class:`draft.llm.LLMClient` (``fake.complete`` /
  ``fake.chat``), or
* as the HTTP transport of a real :class:`draft.llm.LLMClient`
  (``LLMClient(config, transport=fake.transport)`` or ``fake.client()``).

Replies are served from a preset script, one per call.  A reply may be:

* ``str`` -- assistant content, returned unchanged;
* ``LLMResponse`` -- returned as-is;
* ``Mapping`` -- a complete OpenAI-compatible response payload;
* ``BaseException`` -- raised when the reply is consumed (for example
  ``TimeoutError`` or ``OSError``);
* ``FakeHTTPError`` -- an HTTP error status for transport injection;
* a callable accepting the message list and returning any of the above.

Example::

    fake = FakeLLM([fenced_dockerfile(FIXED_DOCKERFILE)])
    client = fake.client()
    reply = client.complete("fix the Dockerfile")
    assert fake.prompts == ["fix the Dockerfile"]

The class deliberately fails when the script is exhausted instead of silently
inventing a reply, so a missing preset is reported as a test failure.  Use
``repeat_last=True`` (repeat the final reply forever) or ``cycle=True``
(restart the script) when a test needs an unbounded model.
"""

from __future__ import annotations

import copy
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from draft.llm import (
    DEFAULT_TIMEOUT,
    HTTPRequest,
    HTTPResponse,
    LLMClient,
    LLMConfig,
    LLMRequestError,
    LLMResponse,
)

FAKE_API_URL = "https://fake-llm.invalid/v1"
FAKE_API_KEY = "fake-key-not-a-secret"
FAKE_MODEL = "fake-llm"

__all__ = [
    "FAKE_API_KEY",
    "FAKE_API_URL",
    "FAKE_MODEL",
    "FakeCall",
    "FakeHTTPError",
    "FakeLLM",
    "FakeLLMError",
    "FakeLLMExhausted",
    "fenced_dockerfile",
]


class FakeLLMError(RuntimeError):
    """The fake was used incorrectly."""


class FakeLLMExhausted(FakeLLMError):
    """The preset reply script ran out of replies."""


class FakeHTTPError(Exception):
    """An HTTP status that the fake transport should return.

    When the fake is used through ``FakeLLM.complete``/``chat`` directly, the
    error is converted into :class:`draft.llm.LLMRequestError`; when used as a
    transport, the status and body are returned to :class:`LLMClient`, which
    performs the normal error handling.
    """

    def __init__(
        self,
        status: int = 500,
        message: str = "fake HTTP error",
        *,
        body: bytes | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.body = body


@dataclass(frozen=True)
class FakeCall:
    """One call made to the fake model."""

    messages: tuple[dict[str, Any], ...]
    timeout: float | None
    system: str | None = None

    @property
    def prompt(self) -> str:
        """Return the last user message, or an empty string."""
        for message in reversed(self.messages):
            if message.get("role") == "user":
                content = message.get("content")
                return content if isinstance(content, str) else str(content)
        return ""


def fenced_dockerfile(text: str, language: str = "Dockerfile") -> str:
    """Wrap a Dockerfile in a Markdown code fence, as DRAFT replies do."""
    return f"```{language}\n{text.strip()}\n```\n"


def _payload_for(content: str, model: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _content_from_payload(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    choice = choices[0]
    if not isinstance(choice, Mapping):
        return ""
    message = choice.get("message")
    if isinstance(message, Mapping):
        content = message.get("content")
        if isinstance(content, str):
            return content
    text = choice.get("text")
    return text if isinstance(text, str) else ""


def _messages_from_request(request: HTTPRequest) -> tuple[dict[str, Any], ...]:
    try:
        body = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FakeLLMError(f"request body is not JSON: {exc}") from exc
    messages = body.get("messages") if isinstance(body, Mapping) else None
    if not isinstance(messages, list):
        raise FakeLLMError("request body has no messages list")
    return tuple(copy.deepcopy(message) for message in messages)


@dataclass
class FakeLLM:
    """A scripted, deterministic replacement for the LLM adapter."""

    replies: Sequence[Any] = field(default_factory=tuple)
    model: str = FAKE_MODEL
    repeat_last: bool = False
    cycle: bool = False
    usage: Mapping[str, Any] | None = None
    calls: list[FakeCall] = field(default_factory=list, init=False)
    requests: list[HTTPRequest] = field(default_factory=list, init=False)
    timeouts: list[float] = field(default_factory=list, init=False)
    served: list[LLMResponse] = field(default_factory=list, init=False)
    _script: list[Any] = field(default_factory=list, init=False)
    _queue: deque[Any] = field(default_factory=deque, init=False)
    _cursor: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.repeat_last and self.cycle:
            raise FakeLLMError("repeat_last and cycle are mutually exclusive")
        script = self._normalize(self.replies)
        for reply in script:
            self._check_reply(reply)
        self._script = script
        self._queue = deque(script)

    @staticmethod
    def _normalize(replies: Any) -> list[Any]:
        if isinstance(replies, (str, bytes, LLMResponse, Mapping, BaseException)):
            return [replies]
        if callable(replies):
            return [replies]
        return list(replies)

    @staticmethod
    def _check_reply(reply: Any) -> None:
        if isinstance(reply, (str, bytes, LLMResponse, Mapping, BaseException)):
            return
        if callable(reply):
            return
        raise TypeError(
            "fake LLM replies must be str, bytes, LLMResponse, Mapping, "
            f"an exception, or a callable; got {type(reply).__name__}"
        )

    def add_reply(self, reply: Any) -> None:
        """Append one reply to the script (also available to ``cycle``)."""
        self._check_reply(reply)
        self._script.append(reply)
        self._queue.append(reply)

    def extend(self, replies: Iterable[Any]) -> None:
        """Append several replies to the script."""
        for reply in replies:
            self.add_reply(reply)

    def reset(self, replies: Iterable[Any] | None = None) -> None:
        """Clear recordings and restart the script (or install a new one)."""
        if replies is not None:
            script = self._normalize(replies)
            for reply in script:
                self._check_reply(reply)
            self._script = script
        self._queue = deque(self._script)
        self._cursor = 0
        self.calls.clear()
        self.requests.clear()
        self.timeouts.clear()
        self.served.clear()

    @property
    def pending(self) -> int:
        """Number of scripted replies not yet consumed."""
        return len(self._queue)

    @property
    def prompts(self) -> list[str]:
        """Prompts recorded from :meth:`complete` and the HTTP transport."""
        return [call.prompt for call in self.calls]

    @property
    def last_prompt(self) -> str | None:
        return self.calls[-1].prompt if self.calls else None

    def _next_item(self) -> Any:
        if self._queue:
            item = self._queue.popleft()
        elif self.repeat_last and self._script:
            item = self._script[-1]
        elif self.cycle and self._script:
            item = self._script[self._cursor % len(self._script)]
            self._cursor += 1
        else:
            raise FakeLLMExhausted(
                f"fake LLM script exhausted after {len(self.served)} repl"
                f"{'y' if len(self.served) == 1 else 'ies'}"
            )

        if callable(item) and not isinstance(item, BaseException):
            return item
        return item

    def _resolve(self, item: Any, messages: Sequence[Mapping[str, Any]]) -> Any:
        if callable(item) and not isinstance(item, BaseException):
            item = item([dict(message) for message in messages])
            self._check_reply(item)
        return item

    def _response_for(
        self,
        item: Any,
        messages: Sequence[Mapping[str, Any]],
        *,
        timeout: float | None,
    ) -> LLMResponse:
        item = self._resolve(item, messages)
        if isinstance(item, FakeHTTPError):
            raise LLMRequestError(
                f"HTTP {item.status}: {item.message}",
                status=item.status,
                endpoint=f"{FAKE_API_URL}/chat/completions",
                detail=item.message,
            )
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, LLMResponse):
            return item
        if isinstance(item, bytes):
            item = item.decode("utf-8", errors="replace")

        if isinstance(item, Mapping):
            payload = copy.deepcopy(dict(item))
            content = _content_from_payload(payload)
        else:
            content = item
            payload = _payload_for(content, self.model)

        if self.usage is not None:
            payload["usage"] = dict(self.usage)
        response = LLMResponse(
            content=content,
            model=self.model,
            status=200,
            endpoint=f"{FAKE_API_URL}/chat/completions",
            finish_reason="stop",
            usage=payload.get("usage") if isinstance(payload.get("usage"), Mapping) else None,
            raw_response=json.dumps(payload, ensure_ascii=False),
            payload=payload,
            latency_seconds=0.0,
        )
        return response

    def _record(self, messages: Sequence[Mapping[str, Any]], timeout: float | None, system: str | None) -> None:
        self.calls.append(
            FakeCall(
                messages=tuple(copy.deepcopy(dict(message)) for message in messages),
                timeout=timeout,
                system=system,
            )
        )

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        timeout: float | None = None,
    ) -> LLMResponse:
        """Return the next scripted reply for one user prompt."""
        messages: list[dict[str, Any]] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages, timeout=timeout)

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        timeout: float | None = None,
    ) -> LLMResponse:
        """Return the next scripted reply for a message list."""
        if not messages:
            raise FakeLLMError("messages must not be empty")
        system = None
        for message in messages:
            if message.get("role") == "system" and isinstance(message.get("content"), str):
                system = message["content"]
                break
        self._record(messages, timeout, system)
        response = self._response_for(self._next_item(), messages, timeout=timeout)
        self.served.append(response)
        return response

    def transport(self, request: HTTPRequest, timeout: float) -> HTTPResponse:
        """Serve a request as an injectable :data:`draft.llm.Transport`."""
        messages = _messages_from_request(request)
        system = next(
            (
                message["content"]
                for message in messages
                if message.get("role") == "system" and isinstance(message.get("content"), str)
            ),
            None,
        )
        self.requests.append(request)
        self.timeouts.append(timeout)
        self._record(messages, timeout, system)

        try:
            item = self._next_item()
        except FakeLLMExhausted:
            raise
        item = self._resolve(item, messages)

        if isinstance(item, FakeHTTPError):
            body = item.body
            if body is None:
                body = json.dumps({"error": {"message": item.message}}).encode("utf-8")
            return HTTPResponse(status=item.status, body=body)
        if isinstance(item, BaseException):
            raise item

        response = self._response_for(item, messages, timeout=timeout)
        self.served.append(response)
        return HTTPResponse(status=response.status, body=response.raw_response.encode("utf-8"))

    def client(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMClient:
        """Return a real :class:`LLMClient` wired to this fake transport."""
        config = LLMConfig(
            api_url=FAKE_API_URL,
            api_key=FAKE_API_KEY,
            model=self.model,
            timeout=timeout,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return LLMClient(config, transport=self.transport)
