#!/usr/bin/env python3
"""Check that the LLM settings in ``.env`` can actually be called.

The checker reads ``LLM_API_URL``, ``LLM_API_KEY`` and ``LLM_MODEL`` from
``.env`` (environment variables take precedence), validates them with the
DRAFT service adapter, and sends one minimal OpenAI-compatible request.

Exit codes:
    0  the settings are present and the model returned a response
    1  the request failed (network, HTTP or response format)
    2  the configuration is invalid or incomplete
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "services" / "draft"))

from draft.llm import (  # noqa: E402
    LLMClient,
    LLMConfigError,
    LLMError,
    display_endpoint,
)

DEFAULT_TIMEOUT = 30.0


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


def _client(env_file: Path, *, explicit: bool) -> LLMClient:
    if env_file.is_file():
        return LLMClient.from_env(env_file=env_file, environ=os.environ)
    if explicit:
        raise LLMConfigError(f"env file not found: {env_file}")
    return LLMClient.from_env(environ=os.environ)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check-llm",
        description="Check LLM_API_URL / LLM_API_KEY / LLM_MODEL from .env.",
    )
    parser.add_argument(
        "--env-file",
        default=None,
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
    parser.add_argument(
        "--show-reply",
        action="store_true",
        help="include the model reply in the output",
    )
    args = parser.parse_args(argv)

    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero")

    env_file = Path(args.env_file).expanduser() if args.env_file else REPO_ROOT / ".env"
    try:
        client = _client(env_file, explicit=args.env_file is not None)
    except LLMConfigError as exc:
        if args.as_json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"result: CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    config = client.config
    endpoint = display_endpoint(config.endpoint)
    if args.dry_run:
        result: dict[str, Any] = {
            "ok": True,
            "dry_run": True,
            "api_url": endpoint,
            "model": config.model,
        }
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print_human(result, dry_run=True)
        return 0

    # Keep the probe cheap without mutating the caller's configuration.
    probe = LLMClient(dataclasses.replace(config, max_tokens=8))
    try:
        response = probe.chat([{"role": "user", "content": "ping"}], timeout=args.timeout)
    except LLMError as exc:
        if args.as_json:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "api_url": endpoint,
                        "model": config.model,
                        "error": str(exc),
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print("LLM check")
            print(f"  api_url: {endpoint}")
            print(f"  model:   {config.model}")
            print("  api_key: set")
            print(f"result: FAILED: {exc}", file=sys.stderr)
        return 1

    reply = " ".join(response.content.split())[:200]
    result = {
        "ok": True,
        "api_url": response.endpoint,
        "model": response.model,
        "status": response.status,
        "latency_seconds": response.latency_seconds,
        "reply": reply,
        "usage": dict(response.usage) if response.usage is not None else None,
    }
    if not args.show_reply:
        result.pop("reply", None)

    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_human(result, dry_run=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
