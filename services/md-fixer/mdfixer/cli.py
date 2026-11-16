"""Command line entry point for the MDFixer service."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import SERVICE, __version__
from .gate import INVALID_PATCH, run_gate, run_transaction
from .guard import RepairError
from .llm import LLMAdapter
from .patch import generate_patch
from .result import scenario_passed, write_scenario_artifacts
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
    generate.add_argument(
        "--llm-from-env",
        action="store_true",
        help="use LLM_API_URL, LLM_API_KEY and LLM_MODEL for complex implicit rules",
    )
    generate.add_argument(
        "--collect-rejections",
        action="store_true",
        help="keep supported findings and report rejected targets instead of aborting",
    )

    validate = sub.add_parser(
        "validate",
        help="run sandbox, build, recheck, accept and rollback validation",
    )
    validate.add_argument("--project", required=True, help="project root with a committed HEAD")
    validate.add_argument("--report", required=True, help="fixed MD report JSON")
    validate.add_argument("--makefile", default="Makefile", help="Makefile path relative to the project")
    validate.add_argument("--build-target", default="app", help="Make target used for build validation")
    validate.add_argument("--clean-target", default="clean", help="Make clean target")
    validate.add_argument("--program", default="app", help="program path relative to the project")
    validate.add_argument("--expected-output", default="1", help="expected program output after patching")
    validate.add_argument(
        "--reject-expected-output",
        default="99",
        help="expected output used by the test-rejection scenario",
    )
    validate.add_argument("--header", default="config.h", help="header used for incremental validation")
    validate.add_argument("--macro", default="VALUE", help="macro changed by incremental validation")
    validate.add_argument("--old-value", default="1", help="original macro value")
    validate.add_argument("--new-value", default="2", help="new macro value")
    validate.add_argument("--commit", default=None, help="expected project HEAD commit")
    validate.add_argument("--configuration-id", default=None, help="expected report configuration")
    validate.add_argument("--timeout", type=int, default=120, help="per-command timeout in seconds")
    validate.add_argument(
        "--only",
        choices=("accept", "reject-test", "reject-patch"),
        default=None,
        help="run one scenario instead of the three-scenario gate",
    )
    validate.add_argument("--patch", default=None, help="candidate patch used by a rejection scenario")
    validate.add_argument("--out", default=None, help="write gate artifacts under this directory")
    validate.add_argument("--json", action="store_true", help="print the validation payload to stdout")
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
        llm = LLMAdapter.from_env() if args.llm_from_env else None
        result = generate_patch(
            project,
            Path(args.report).expanduser(),
            makefile=args.makefile,
            commit=args.commit,
            configuration_id=args.configuration_id,
            llm=llm,
            collect_rejections=args.collect_rejections,
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


def _read_patch(value: str) -> str:
    target = Path(value).expanduser().resolve()
    if any(
        part.lower() == ".env" or part.lower().startswith(".env.")
        for part in target.parts
    ):
        raise ValueError("patch input must not be a sensitive path")
    if not target.is_file():
        raise ValueError(f"patch input not found: {target}")
    try:
        return target.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read patch input: {exc}") from exc


def _print_validation(scenario: str, validation: dict) -> None:
    checks = " ".join(
        f"{name}={'true' if value else 'false'}"
        for name, value in validation["checks"].items()
    )
    print(
        "validate {scenario}: accepted={accepted} source_unchanged={source} "
        "rollback={rollback} {checks}".format(
            scenario=scenario,
            accepted="true" if validation["accepted"] else "false",
            source="true" if validation["source_unchanged"] else "false",
            rollback="true" if validation["rollback"] else "false",
            checks=checks,
        )
    )
    if validation.get("error"):
        print(f"error: {validation['error']}", file=sys.stderr)


def _cmd_validate(args: argparse.Namespace) -> int:
    project = Path(args.project).expanduser().resolve()
    report = Path(args.report).expanduser()
    try:
        output = _smoke_output_path(args.out) if args.out else None
        if output is not None and (
            output == project or output.is_relative_to(project)
        ):
            raise ValueError("--out must be outside the project")
        common = {
            "makefile": args.makefile,
            "build_target": args.build_target,
            "clean_target": args.clean_target,
            "program": args.program,
            "header": args.header,
            "macro": args.macro,
            "old_value": args.old_value,
            "new_value": args.new_value,
            "commit": args.commit,
            "configuration_id": args.configuration_id,
            "timeout": args.timeout,
        }
        if args.only:
            if args.only == "accept" and args.patch:
                raise ValueError("--patch is only supported for rejection scenarios")
            patch = None
            expected = args.expected_output
            if args.only == "reject-patch":
                patch = _read_patch(args.patch) if args.patch else INVALID_PATCH
            elif args.only == "reject-test":
                expected = args.reject_expected_output
            transaction = run_transaction(
                project,
                report,
                scenario=args.only,
                expected_output=expected,
                patch=patch,
                **common,
            )
            passed = scenario_passed(args.only, transaction.validation)
            if output is not None:
                write_scenario_artifacts(
                    output,
                    args.only,
                    validation=transaction.validation,
                    build_log=transaction.build_log,
                    incremental_log=transaction.incremental_log,
                    recheck=transaction.recheck,
                )
            if args.json:
                print(json.dumps(transaction.to_payload(), ensure_ascii=False, indent=2))
            else:
                _print_validation(args.only, transaction.validation)
            return 0 if passed else 1

        if args.patch:
            raise ValueError("--patch requires --only reject-patch")
        result = run_gate(
            project,
            report,
            output_dir=output,
            expected_output=args.expected_output,
            reject_expected_output=args.reject_expected_output,
            **common,
        )
        if args.json:
            print(json.dumps(result.to_payload(), ensure_ascii=False, indent=2))
        else:
            for scenario in result.scenarios:
                _print_validation(scenario["scenario"], scenario["validation"])
            print(
                "validate: passed={} rows={}{}".format(
                    "true" if result.passed else "false",
                    result.rows,
                    f" output={result.output_dir}" if result.output_dir else "",
                )
            )
        return 0 if result.passed else 1
    except (RepairError, ValueError, OSError) as exc:
        code = getattr(exc, "code", "INVALID_INPUT")
        if args.json:
            print(
                json.dumps(
                    {"status": "REJECTED", "code": code, "message": str(exc)},
                    ensure_ascii=False,
                )
            )
        else:
            print(f"validate rejected: {code}: {exc}", file=sys.stderr)
        return 2


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
    if args.command == "validate":
        return _cmd_validate(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
