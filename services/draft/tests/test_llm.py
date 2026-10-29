"""Unit tests for the injectable DRAFT LLM adapter."""

from __future__ import annotations

import json

import pytest

from draft.llm import (
    DEFAULT_TIMEOUT,
    HTTPResponse,
    LLMClient,
    LLMConfig,
    LLMConfigError,
    LLMError,
    LLMRequestError,
    LLMResponseError,
    chat_endpoint,
    display_endpoint,
    load_config,
)


class RecordingTransport:
    """Fake HTTP transport that records the request and returns fixed data."""

    def __init__(self, status: int = 200, payload=None, *, body: bytes | None = None):
        self.status = status
        self.payload = payload
        self.body = body
        self.requests = []
        self.timeouts = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self.body is not None:
            return HTTPResponse(self.status, self.body)
        return HTTPResponse(self.status, json.dumps(self.payload).encode("utf-8"))


def _config(**overrides) -> LLMConfig:
    values = {
        "api_url": "https://api.example.com/v1",
        "api_key": "sk-test-secret",
        "model": "test-model",
    }
    values.update(overrides)
    return LLMConfig(**values)


def _ok_payload(content: str = "candidate Dockerfile\n", **extra) -> dict:
    payload = {
        "id": "chatcmpl-1",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
    }
    payload.update(extra)
    return payload


def test_load_config_from_environment():
    config = load_config(
        environ={
            "LLM_API_URL": "https://api.example.com/v1",
            "LLM_API_KEY": "sk-test-secret",
            "LLM_MODEL": "test-model",
        }
    )

    assert config.api_url == "https://api.example.com/v1"
    assert config.api_key == "sk-test-secret"
    assert config.model == "test-model"
    assert config.timeout == DEFAULT_TIMEOUT
    assert config.endpoint == "https://api.example.com/v1/chat/completions"


def test_environment_values_override_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# comment",
                "export LLM_API_URL='https://file.example.com/v1'",
                "LLM_API_KEY=file-secret",
                'LLM_MODEL="file-model"',
                "LLM_TIMEOUT=9",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(
        env_file=env_file,
        environ={"LLM_API_KEY": "env-secret", "LLM_MODEL": "env-model"},
    )

    assert config.api_url == "https://file.example.com/v1"
    assert config.api_key == "env-secret"
    assert config.model == "env-model"
    assert config.timeout == 9


def test_load_config_missing_required_values():
    with pytest.raises(LLMConfigError) as excinfo:
        load_config(environ={})
    message = str(excinfo.value)
    assert "LLM_API_URL" in message
    assert "LLM_API_KEY" in message
    assert "LLM_MODEL" in message


def test_load_config_missing_env_file(tmp_path):
    with pytest.raises(LLMConfigError):
        load_config(env_file=tmp_path / "missing.env", environ={})


@pytest.mark.parametrize(
    "url",
    ["", "ftp://api.example.com", "api.example.com", "https://user:pass@api.example.com"],
)
def test_invalid_api_url_rejected(url):
    with pytest.raises(LLMConfigError):
        _config(api_url=url)


def test_invalid_optional_values_rejected():
    with pytest.raises(LLMConfigError):
        _config(max_tokens=0)
    with pytest.raises(LLMConfigError):
        _config(temperature=3)
    with pytest.raises(LLMConfigError):
        load_config(
            environ={
                "LLM_API_URL": "https://api.example.com/v1",
                "LLM_API_KEY": "sk-test-secret",
                "LLM_MODEL": "test-model",
                "LLM_MAX_TOKENS": "many",
            }
        )


def test_config_repr_hides_api_key():
    assert "sk-test-secret" not in repr(_config())


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://api.example.com", "https://api.example.com/v1/chat/completions"),
        ("https://api.example.com/", "https://api.example.com/v1/chat/completions"),
        ("https://api.example.com/v1", "https://api.example.com/v1/chat/completions"),
        (
            "https://api.example.com/v1/chat/completions",
            "https://api.example.com/v1/chat/completions",
        ),
        (
            "https://api.example.com/openai/v1/chat/completions",
            "https://api.example.com/openai/v1/chat/completions",
        ),
        (
            "https://api.example.com/custom",
            "https://api.example.com/custom/chat/completions",
        ),
    ],
)
def test_chat_endpoint_variants(url, expected):
    assert chat_endpoint(url) == expected


def test_display_endpoint_removes_query():
    assert (
        display_endpoint("https://api.example.com/v1/chat/completions?key=secret")
        == "https://api.example.com/v1/chat/completions"
    )


def test_complete_returns_unmodified_reply_and_records_request():
    transport = RecordingTransport(payload=_ok_payload("line one\nline two\n"))
    client = LLMClient(_config(timeout=7), transport=transport)

    result = client.complete("fix this Dockerfile")

    assert result.content == "line one\nline two\n"
    assert result.text == result.content
    assert result.model == "test-model"
    assert result.status == 200
    assert result.finish_reason == "stop"
    assert result.usage == {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}
    assert result.endpoint == "https://api.example.com/v1/chat/completions"
    assert result.raw_response.startswith("{")
    assert transport.timeouts == [7]

    request = transport.requests[0]
    body = json.loads(request.body.decode("utf-8"))
    assert request.method == "POST"
    assert request.headers["Authorization"] == "Bearer sk-test-secret"
    assert body["model"] == "test-model"
    assert body["messages"] == [{"role": "user", "content": "fix this Dockerfile"}]
    assert body["stream"] is False


def test_complete_includes_system_message_and_optional_parameters():
    transport = RecordingTransport(payload=_ok_payload())
    client = LLMClient(_config(max_tokens=64, temperature=0.2), transport=transport)

    client.complete("prompt", system="You are a build engineer.")

    body = json.loads(transport.requests[0].body.decode("utf-8"))
    assert body["messages"] == [
        {"role": "system", "content": "You are a build engineer."},
        {"role": "user", "content": "prompt"},
    ]
    assert body["max_tokens"] == 64
    assert body["temperature"] == 0.2


def test_complete_uses_reasoning_content_when_content_is_missing():
    payload = _ok_payload()
    payload["choices"][0]["message"] = {"role": "assistant", "reasoning_content": "fallback"}
    client = LLMClient(_config(), transport=RecordingTransport(payload=payload))

    assert client.complete("prompt").content == "fallback"


def test_complete_uses_reasoning_content_when_content_is_empty():
    payload = _ok_payload()
    payload["choices"][0]["message"] = {
        "role": "assistant",
        "content": "",
        "reasoning_content": "fallback",
    }
    client = LLMClient(_config(), transport=RecordingTransport(payload=payload))

    assert client.complete("prompt").content == "fallback"


def test_http_error_is_reported_and_api_key_is_redacted():
    transport = RecordingTransport(
        status=401,
        payload={"error": {"message": "bad key sk-test-secret"}},
    )
    client = LLMClient(_config(), transport=transport)

    with pytest.raises(LLMRequestError) as excinfo:
        client.complete("prompt")

    assert excinfo.value.status == 401
    assert "HTTP 401" in str(excinfo.value)
    assert "sk-test-secret" not in str(excinfo.value)
    assert "***" in str(excinfo.value)


def test_http_error_with_non_json_body_is_reported():
    transport = RecordingTransport(status=502, body=b"upstream unavailable")
    client = LLMClient(_config(), transport=transport)

    with pytest.raises(LLMRequestError) as excinfo:
        client.complete("prompt")

    assert excinfo.value.status == 502
    assert "upstream unavailable" in str(excinfo.value)


def test_malformed_json_is_rejected():
    transport = RecordingTransport(status=200, body=b"not json")
    client = LLMClient(_config(), transport=transport)

    with pytest.raises(LLMResponseError):
        client.complete("prompt")


def test_response_without_choices_is_rejected():
    transport = RecordingTransport(payload={"model": "test-model", "choices": []})
    client = LLMClient(_config(), transport=transport)

    with pytest.raises(LLMResponseError):
        client.complete("prompt")


def test_network_error_is_wrapped():
    def transport(request, timeout):
        raise OSError("connection refused")

    client = LLMClient(_config(), transport=transport)

    with pytest.raises(LLMRequestError) as excinfo:
        client.complete("prompt")

    assert "connection refused" in str(excinfo.value)


def test_timeout_is_wrapped():
    def transport(request, timeout):
        raise TimeoutError("timed out")

    client = LLMClient(_config(), transport=transport)

    with pytest.raises(LLMRequestError) as excinfo:
        client.complete("prompt")

    assert "timed out" in str(excinfo.value)


def test_empty_messages_rejected():
    client = LLMClient(_config(), transport=RecordingTransport(payload=_ok_payload()))

    with pytest.raises(LLMError):
        client.chat([])
