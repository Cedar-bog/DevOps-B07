"""Command line entry point for the MDFixer service."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import SERVICE, __version__
from .smoke import find_repo_root, run_smoke, write_result


def _default_fixture_dir() -> Path:
    env = os.environ.get("MDFIXER_FIXTURE_DIR")
    if env:
        return Path(env)
    return find_repo_root(Path(__file__)) / "fixtures" / "md-fixer"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=SERVICE, description="MDFixer service command line."
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("version", help="print the service version")

    smoke = sub.add_parser(
        "smoke",
        help="check the MDFixer fixture and fixed MD report",
    )
    smoke.add_argument(
        "--fixture-dir",
        default=None,
        help="fixture directory (default: $MDFIXER_FIXTURE_DIR or <repo>/fixtures/md-fixer)",
    )
    smoke.add_argument(
        "--report",
        default=None,
        help="fixed MD report (default: <fixture-dir>/fixed-md-report.json)",
    )
    smoke.add_argument(
        "--output",
        default=None,
        help="write the JSON result to this path; omit or use '-' to print to stdout",
    )
    return parser


def _cmd_smoke(args: argparse.Namespace) -> int:
    fixture_dir = (
        Path(args.fixture_dir).expanduser()
        if args.fixture_dir
        else _default_fixture_dir()
    )
    report = Path(args.report).expanduser() if args.report else None
    result = run_smoke(fixture_dir, report=report)
    payload = result.to_payload()

    if args.output and args.output != "-":
        target = write_result(result, args.output)
        print(f"smoke result written to {target}", file=sys.stderr)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    passed = sum(1 for check in result.checks if check.ok)
    print(
        "smoke: {passed}/{total} checks passed ok={ok}".format(
            passed=passed,
            total=len(result.checks),
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
