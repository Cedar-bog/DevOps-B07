"""Tests for the deterministic fake LLM used by the DRAFT test suite."""

from __future__ import annotations

import json

import pytest

from draft.llm import LLMRequestError, LLMResponse
from fake_llm import (
    FakeHTTPError,
    FakeLLM,
    FakeLLMExhausted,
    fenced_dockerfile,
)

FIXED = """FROM python:3.13-slim
WORKDIR /work
COPY main.c Makefile ./
RUN apt-get update && apt-get install -y --no-install-recommends make gcc libc6-dev
RUN make
CMD ["./hello"]
"""


def test_scripted_replies_are_returned_in_order():
    fake = FakeLLM(["first\n", "second\n"])

    assert fake.complete("one").content == "first\n"
    assert fake.complete("two", system="be terse").content == "second\n"
    assert fake.prompts == ["one", "two"]
    assert fake.calls[1].system == "be terse"
    assert fake.calls[1].messages[0] == {"role": "system", "content": "be terse"}
    assert fake.served[0].raw_response.startswith("{")
    assert fake.pending == 0


def test_fenced_dockerfile_reply_is_preserved_exactly():
    text = fenced_dockerfile(FIXED)
    fake = FakeLLM([text])

    assert fake.complete("fix").content == text


def test_exhausted_script_fails_instead_of_inventing_a_reply():
    fake = FakeLLM(["only"])

    fake.complete("first")

    with pytest.raises(FakeLLMExhausted):
        fake.complete("second")


def test_repeat_last_and_cycle_modes():
    repeat = FakeLLM(["same"], repeat_last=True)
    assert [repeat.complete("p").content for _ in range(3)] == ["same", "same", "same"]

    cycle = FakeLLM(["a", "b"], cycle=True)
    assert [cycle.complete("p").content for _ in range(5)] == ["a", "b", "a", "b", "a"]


def test_repeat_last_without_replies_still_reports_exhaustion():
    with pytest.raises(FakeLLMExhausted):
        FakeLLM([], repeat_last=True).complete("p")


def test_repeat_last_and_cycle_are_mutually_exclusive():
    with pytest.raises(Exception):
        FakeLLM(["x"], repeat_last=True, cycle=True)


def test_transport_injection_through_real_client():
    fake = FakeLLM(["reply", "next"])
    client = fake.client()

    result = client.complete("prompt", system="system")

    assert result.content == "reply"
    assert result.model == fake.model
    assert result.status == 200
    assert len(fake.requests) == 1
    request = fake.requests[0]
    body = json.loads(request.body.decode("utf-8"))
    assert body["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "prompt"},
    ]
    assert request.headers["Authorization"].startswith("Bearer fake-")
    assert fake.timeouts == [client.config.timeout]


def test_transport_http_error_is_handled_by_client():
    fake = FakeLLM([FakeHTTPError(429, "slow down")])
    client = fake.client()

    with pytest.raises(LLMRequestError) as excinfo:
        client.complete("prompt")

    assert excinfo.value.status == 429
    assert "slow down" in str(excinfo.value)


def test_direct_complete_converts_http_error():
    fake = FakeLLM([FakeHTTPError(503, "unavailable")])

    with pytest.raises(LLMRequestError) as excinfo:
        fake.complete("prompt")

    assert excinfo.value.status == 503


def test_transport_os_and_timeout_errors_are_wrapped_by_client():
    fake = FakeLLM([OSError("connection refused"), TimeoutError("timed out")])
    client = fake.client()

    with pytest.raises(LLMRequestError, match="connection refused"):
        client.complete("first")
    with pytest.raises(LLMRequestError, match="timed out"):
        client.complete("second")


def test_callable_reply_receives_messages():
    seen = []

    def reply(messages):
        seen.append(messages)
        return "dynamic"

    fake = FakeLLM([reply])
    assert fake.complete("prompt").content == "dynamic"
    assert seen == [[{"role": "user", "content": "prompt"}]]


def test_payload_reply_and_llm_response_passthrough():
    payload = {
        "model": "payload-model",
        "choices": [{"message": {"role": "assistant", "content": "payload"}}],
        "usage": {"total_tokens": 3},
    }
    existing = LLMResponse(
        content="existing",
        model="m",
        status=200,
        endpoint="https://fake.invalid/v1/chat/completions",
    )
    fake = FakeLLM([payload, existing])

    assert fake.complete("p1").content == "payload"
    assert fake.complete("p2").content == "existing"


def test_reset_clears_recordings_and_restarts_script():
    fake = FakeLLM(["a"])
    fake.complete("p")
    fake.reset(["b"])

    assert fake.calls == []
    assert fake.requests == []
    assert fake.complete("q").content == "b"
    assert fake.prompts == ["q"]


def test_invalid_reply_type_is_rejected_early():
    with pytest.raises(TypeError):
        FakeLLM([42])
