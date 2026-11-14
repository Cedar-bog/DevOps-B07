"""Command line entry point for the MDFixer service."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import SERVICE, __version__
from .guard import RepairError
from .patch import generate_patch
from .smoke import find_repo_root, run_smoke, write_result


def _default_fixture_dir() -> Path:
    env = os.environ.get("MDFIXER_FIXTURE_DIR")
    if env:
        return Path(env)
    return find_repo_root(Path(__file__)) / "fixtures" / "md-fixer"


def _smoke_output_path(value: str) -> Path:
    target = Path(value).expanduser().resolve()
    if any(
        part.lower() == ".env" or part.lower().startswith(".env.")
        for part in target.parts
    ):
        raise ValueError("output must not be a sensitive path")
    repo = find_repo_root(Path(__file__)).resolve()
    if target.is_relative_to(repo):
        relative = target.relative_to(repo)
        if not relative.parts or relative.parts[0] not in {"work", "evidence"}:
            raise ValueError("output must be outside the source tree or under work/evidence")
    return target


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

    generate = sub.add_parser(
        "generate",
        help="generate a candidate patch and repair plan from a fixed MD report",
    )
    generate.add_argument("--project", required=True, help="project root with a committed HEAD")
    generate.add_argument("--report", required=True, help="fixed MD report JSON")
    generate.add_argument("--makefile", default="Makefile", help="Makefile path relative to the project")
    generate.add_argument("--commit", default=None, help="expected project HEAD commit")
    generate.add_argument("--configuration-id", default=None, help="expected report configuration")
    generate.add_argument("--patch-out", default=None, help="write the Git patch to this path")
    generate.add_argument("--plan-out", default=None, help="write the repair plan JSON to this path")
    generate.add_argument("--json", action="store_true", help="print the candidate payload to stdout")
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
        try:
            target = _smoke_output_path(args.output)
            target = write_result(result, target)
        except (ValueError, OSError) as exc:
            print(f"smoke output error: {exc}", file=sys.stderr)
            return 2
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


def _output_path(value: str, project: Path, label: str) -> Path:
    target = Path(value).expanduser().resolve()
    if any(
        part.lower() == ".env" or part.lower().startswith(".env.")
        for part in target.parts
    ):
        raise ValueError(f"{label} must not be a sensitive path")
    if target.is_relative_to(project):
        raise ValueError(f"{label} must be outside the project")
    return target


def _write_output(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="")


def _cmd_generate(args: argparse.Namespace) -> int:
    project = Path(args.project).expanduser().resolve()
    try:
        result = generate_patch(
            project,
            Path(args.report).expanduser(),
            makefile=args.makefile,
            commit=args.commit,
            configuration_id=args.configuration_id,
        )
        if args.patch_out:
            _write_output(_output_path(args.patch_out, project, "patch output"), result.git_patch)
        if args.plan_out:
            plan_text = json.dumps(result.plan.to_payload(), ensure_ascii=False, indent=2) + "\n"
            _write_output(_output_path(args.plan_out, project, "plan output"), plan_text)
    except (RepairError, ValueError, OSError) as exc:
        code = getattr(exc, "code", "INVALID_INPUT")
        if args.json:
            print(json.dumps({"status": "REJECTED", "code": code, "message": str(exc)}, ensure_ascii=False))
        else:
            print(f"generate rejected: {code}: {exc}", file=sys.stderr)
        return 2

    payload = result.to_payload()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if result.git_patch:
            print(result.git_patch, end="")
        print(
            "generate: {} change(s), patch={} byte(s)".format(
                len(result.plan.changes), len(result.git_patch)
            ),
            file=sys.stderr,
        )
    return 0


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
    if args.command == "generate":
        return _cmd_generate(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
