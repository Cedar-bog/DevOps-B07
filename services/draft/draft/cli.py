"""Command line entry point for the DRAFT service."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .smoke import DEFAULT_TIMEOUT, SmokeError, run_smoke, write_result

SERVICE = "draft"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_fixture_dir() -> Path:
    env = os.environ.get("DRAFT_FIXTURE_DIR")
    if env:
        return Path(env)
    return _repo_root() / "fixtures" / "draft"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=SERVICE, description="DRAFT service command line.")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("version", help="print the service version")

    smoke = sub.add_parser(
        "smoke",
        help="build the DRAFT fixture and expect a locatable failure",
    )
    smoke.add_argument(
        "--fixture-dir",
        default=None,
        help="fixture directory (default: $DRAFT_FIXTURE_DIR or <repo>/fixtures/draft)",
    )
    smoke.add_argument("--image-tag", default=None, help="image tag for the smoke build")
    smoke.add_argument("--docker", default="docker", help="docker executable (default: docker)")
    smoke.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"build timeout in seconds (default: {DEFAULT_TIMEOUT:g})",
    )
    smoke.add_argument(
        "--output",
        default=None,
        help="write the JSON result to this path; omit or use '-' to print to stdout",
    )

    return parser


def _cmd_smoke(args: argparse.Namespace) -> int:
    fixture_dir = Path(args.fixture_dir) if args.fixture_dir else _default_fixture_dir()
    try:
        result = run_smoke(
            fixture_dir,
            docker=args.docker,
            image_tag=args.image_tag,
            timeout=args.timeout,
        )
    except SmokeError as exc:
        print(f"smoke error: {exc}", file=sys.stderr)
        return 1

    payload = json.dumps(result.to_payload(), ensure_ascii=False, indent=2)
    if args.output and args.output != "-":
        target = write_result(result, args.output)
        print(f"smoke result written to {target}", file=sys.stderr)
    else:
        print(payload)

    observed = result.to_payload()["observed"]
    print(
        "smoke: exit_code={exit_code} signature={signature} ok={ok}".format(
            exit_code=observed["exit_code"],
            signature="hit" if observed["log_has_signature"] else "miss",
            ok="true" if result.ok else "false",
        ),
        file=sys.stderr,
    )
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(sys.stderr)
        return 2
    if args.command == "version":
        print(f"{SERVICE} {__version__}")
        return 0
    if args.command == "smoke":
        return _cmd_smoke(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
