#!/usr/bin/env python3
"""Check that the LLM settings in ``.env`` can actually be called.

The checker reads ``LLM_API_URL``, ``LLM_API_KEY`` and ``LLM_MODEL`` from
``.env`` (environment variables take precedence), validates them, and sends
one minimal OpenAI-compatible ``/chat/completions`` request.  The API key is
never printed and is redacted from error messages.

Exit codes:
    0  the settings are present and the model returned a response
    1  the request failed (network, HTTP or response format)
    2  the configuration is invalid or incomplete
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("LLM_API_URL", "LLM_API_KEY", "LLM_MODEL")
DEFAULT_TIMEOUT = 30.0
MAX_ERROR_CHARS = 600
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ConfigError(Exception):
    """Invalid or incomplete LLM configuration."""


class CallError(Exception):
    """The LLM endpoint could not be called successfully."""


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a simple ``KEY=VALUE`` file without executing shell code."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
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


def load_settings(env_file: Path) -> dict[str, str]:
    """Load settings, letting the process environment override ``.env``."""
    file_values: dict[str, str] = {}
    if env_file.is_file():
        try:
            file_values = parse_env_file(env_file)
        except OSError as exc:
            raise ConfigError(f"cannot read {env_file}: {exc}") from exc
    elif env_file != REPO_ROOT / ".env":
        raise ConfigError(f"env file not found: {env_file}")

    settings: dict[str, str] = {}
    for key in REQUIRED:
        value = os.environ.get(key, "").strip() or file_values.get(key, "").strip()
        settings[key] = value

    missing = [key for key, value in settings.items() if not value]
    if missing:
        where = env_file if env_file.is_file() else "environment"
        raise ConfigError(
            f"missing {', '.join(missing)} in {where}; "
            "copy .env.example to .env and fill in the LLM settings"
        )

    parsed = urllib.parse.urlsplit(settings["LLM_API_URL"])
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(
            "LLM_API_URL must be an absolute http(s) URL, "
            "for example https://api.deepseek.com/v1"
        )
    return settings


def chat_endpoint(api_url: str) -> str:
    """Turn a base URL into an OpenAI-compatible chat completions URL."""
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


def display_url(api_url: str) -> str:
    """Return a URL without query or fragment, which may contain secrets."""
    parsed = urllib.parse.urlsplit(api_url)
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "", "")
    )


def redact(text: str, secret: str) -> str:
    if secret:
        text = text.replace(secret, "***")
    return re.sub(r"(?i)(bearer\s+)[^\s\"']+", r"\1***", text)


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


def call_llm(settings: dict[str, str], timeout: float) -> dict[str, Any]:
    endpoint = chat_endpoint(settings["LLM_API_URL"])
    key = settings["LLM_API_KEY"]
    payload = {
        "model": settings["LLM_MODEL"],
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
        "stream": False,
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "devops-b07-llm-check/1.0",
        },
    )

    started = time.monotonic()
    status: int | None = None
    body = ""
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise CallError(redact(f"network error: {exc.reason}", key)) from exc
    except TimeoutError as exc:
        raise CallError(f"request timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise CallError(redact(f"request failed: {exc}", key)) from exc
    latency = time.monotonic() - started

    try:
        data = json.loads(body) if body else {}
    except json.JSONDecodeError:
        data = None

    if status is None or not 200 <= status < 300:
        detail = _error_message(data) if data is not None else body.strip()
        detail = redact(detail, key)[:MAX_ERROR_CHARS]
        raise CallError(f"HTTP {status}: {detail or 'empty response'}")

    if not isinstance(data, dict):
        snippet = redact(body.strip(), key)[:MAX_ERROR_CHARS]
        raise CallError(f"response is not a JSON object: {snippet or 'empty body'}")

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        detail = _error_message(data)
        raise CallError(
            f"response contains no choices: {redact(detail, key) or 'unexpected JSON'}"
        )

    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = ""
    if isinstance(message, dict):
        content = message.get("content") or message.get("reasoning_content") or ""
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    content = " ".join(content.split())

    usage = data.get("usage")
    return {
        "ok": True,
        "api_url": display_url(endpoint),
        "model": settings["LLM_MODEL"],
        "status": status,
        "latency_seconds": round(latency, 3),
        "reply": content[:200],
        "usage": usage if isinstance(usage, dict) else None,
    }


def print_human(result: dict[str, Any], *, dry_run: bool) -> None:
    print("LLM check")
    print(f"  api_url: {result['api_url']}")
    print(f"  model:   {result['model']}")
    print("  api_key: set")
    if dry_run:
        print("  request: skipped (--dry-run)")
        print("result: CONFIG OK")
        return
    print(f"  status:  HTTP {result['status']}")
    print(f"  latency: {result['latency_seconds']:.3f}s")
    if result.get("reply"):
        print(f"  reply:   {result['reply']}")
    print("result: OK")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check-llm",
        description="Check LLM_API_URL / LLM_API_KEY / LLM_MODEL from .env.",
    )
    parser.add_argument(
        "--env-file",
        default=str(REPO_ROOT / ".env"),
        help="environment file to read (default: <repo>/.env)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"request timeout in seconds (default: {DEFAULT_TIMEOUT:g})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate the configuration without sending a request",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="print a machine-readable JSON result",
    )
    args = parser.parse_args(argv)

    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")

    env_file = Path(args.env_file).expanduser()
    try:
        settings = load_settings(env_file)
    except ConfigError as exc:
        if args.as_json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"result: CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    endpoint = chat_endpoint(settings["LLM_API_URL"])
    if args.dry_run:
        result: dict[str, Any] = {
            "ok": True,
            "dry_run": True,
            "api_url": display_url(endpoint),
            "model": settings["LLM_MODEL"],
        }
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_human(result, dry_run=True)
        return 0

    try:
        result = call_llm(settings, args.timeout)
    except CallError as exc:
        if args.as_json:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "api_url": display_url(endpoint),
                        "model": settings["LLM_MODEL"],
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print("LLM check")
            print(f"  api_url: {display_url(endpoint)}")
            print(f"  model:   {settings['LLM_MODEL']}")
            print("  api_key: set")
            print(f"result: FAILED: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_human(result, dry_run=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
