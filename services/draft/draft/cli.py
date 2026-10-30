"""Command line entry point for the DRAFT service."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .pipeline import DEFAULT_BUILD_TIMEOUT, DEFAULT_VERIFY_TIMEOUT, load_repair_llm, run_repair
from .smoke import DEFAULT_TIMEOUT, SmokeError, run_smoke, write_result

SERVICE = "draft"


def _repo_root() -> Path:
    # Both the checkout and the service image contain fixtures/draft, at
    # different depths relative to this module.
    for parent in Path(__file__).resolve().parents:
        if (parent / "fixtures" / "draft").is_dir():
            return parent
    return Path.cwd()


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

    repair = sub.add_parser("repair", help="automatically repair and verify a Dockerfile")
    repair.add_argument("--fixture-dir", default=None)
    repair.add_argument("--out", default=None, help="new evidence directory (default: work/<UTC timestamp>)")
    repair.add_argument("--initial-dockerfile", default="Dockerfile.broken")
    repair.add_argument("--mode", choices=("error", "context"), default="context")
    repair.add_argument("--max-iterations", type=int, default=3)
    repair.add_argument("--build-timeout", type=float, default=DEFAULT_BUILD_TIMEOUT)
    repair.add_argument("--verify-timeout", type=float, default=DEFAULT_VERIFY_TIMEOUT)
    repair.add_argument("--expected-output", default="hello E3")
    repair.add_argument("--docker", default="docker", help="Docker executable, without shell arguments")
    source = repair.add_mutually_exclusive_group()
    source.add_argument("--candidate", type=Path, help="use a saved Dockerfile without calling an LLM")
    source.add_argument("--replay", action="store_true", help="use fixtures/ci-candidates/*.Dockerfile")
    repair.add_argument("--no-cache", action="store_true")
    repair.add_argument("--json", action="store_true", help="print result.json to stdout")
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


def _cmd_repair(args: argparse.Namespace) -> int:
    fixture = Path(args.fixture_dir) if args.fixture_dir else _default_fixture_dir()
    try:
        llm = load_repair_llm(
            env_file=_repo_root() / ".env", candidate=args.candidate,
            replay_dir=_repo_root() / "fixtures" / "ci-candidates" if args.replay else None,
        )
        outcome = run_repair(
            fixture, out_dir=args.out, llm=llm, initial_dockerfile=args.initial_dockerfile,
            prompt_mode=args.mode, docker=args.docker, max_iterations=args.max_iterations,
            build_timeout=args.build_timeout, verify_timeout=args.verify_timeout,
            expected_output=args.expected_output, no_cache=args.no_cache,
        )
    except (ValueError, OSError) as exc:
        print(f"repair configuration error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(outcome.result_path.read_text(encoding="utf-8"), end="")
    print(f"repair: {outcome.status}: {outcome.message}\nresult: {outcome.result_path}", file=sys.stderr)
    return 0 if outcome.ok() else 1


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
    if args.command == "repair":
        return _cmd_repair(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
