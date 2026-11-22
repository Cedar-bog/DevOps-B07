#!/usr/bin/env python3
"""Run the E10 three-scenario gate and write CI-ready artifacts."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "e10"
SERVICE = ROOT / "services" / "md-fixer"
DRAFT = ROOT / "services" / "draft"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "git", "-C", str(root),
            "-c", "user.name=E10 CI",
            "-c", "user.email=e10@example.invalid",
            "-c", "commit.gpgsign=false",
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _tool(command: str) -> str:
    try:
        result = subprocess.run(
            [command, "--version"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    line = (result.stdout or result.stderr).splitlines()
    return line[0].strip() if line else "unavailable"


def _report(commit: str, line: int, declaration: str) -> dict:
    return {
        "schema_version": "1.0",
        "repository": {
            "url": "https://github.com/Cedar-bog/DevOps-B07",
            "commit": commit,
        },
        "configuration_id": "e10-ci",
        "producer_job_id": "job-e10-ci",
        "findings": [
            {
                "finding_id": "MISSING-main-o-config-h",
                "type": "MISSING",
                "target": "main.o",
                "dependency": "config.h",
                "commit": commit,
                "position": {"file": "Makefile", "line": line, "declaration": declaration},
                "detector": "INSTRUCTOR_ORACLE",
            },
            {
                "finding_id": "REDUNDANT-main-o-unused-h",
                "type": "REDUNDANT",
                "target": "main.o",
                "dependency": "unused.h",
                "commit": commit,
                "position": {"file": "Makefile", "line": line, "declaration": declaration},
                "detector": "INSTRUCTOR_ORACLE",
            },
        ],
    }


def _prepare_project(out: Path) -> tuple[Path, dict]:
    project = out / "project"
    shutil.copytree(FIXTURE, project)
    if any(item.returncode for item in (_git(project, "init", "-q"), _git(project, "add", "."))):
        raise RuntimeError("cannot initialize the E10 project")
    commit_result = _git(project, "commit", "-qm", "e10 input")
    if commit_result.returncode:
        raise RuntimeError(commit_result.stderr.strip() or "cannot commit the E10 project")
    commit = _git(project, "rev-parse", "HEAD").stdout.strip()
    text = (project / "Makefile").read_text(encoding="utf-8")
    line = next(
        index for index, value in enumerate(text.splitlines(), 1)
        if value.startswith("main.o:")
    )
    return project, _report(commit, line, text.splitlines()[line - 1].strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None, help="artifact directory (default: work/e10-<timestamp>)")
    args = parser.parse_args()
    out = Path(args.out).expanduser().resolve() if args.out else ROOT / "work" / f"e10-{_stamp()}"
    out.mkdir(parents=True, exist_ok=True)
    project, report = _prepare_project(out)
    report_path = out / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    sys.path.insert(0, str(SERVICE))
    sys.path.insert(0, str(DRAFT))
    from mdfixer.gate import run_gate

    summary: dict = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip(),
        "project_commit": report["repository"]["commit"],
        "python": sys.version,
        "platform": platform.platform(),
        "make": _tool("make"),
        "gcc": _tool("gcc"),
    }
    (out / "command.txt").write_text(
        "python scripts/run_e10_gate.py --out " + str(out) + "\n", encoding="utf-8"
    )

    try:
        result = run_gate(project, report, output_dir=out / "gate")
        summary["gate"] = result.to_payload()
        summary["exit_code"] = 0 if result.passed else 1
    except Exception as exc:
        summary["gate"] = None
        summary["exit_code"] = 1
        summary["error"] = f"{type(exc).__name__}: {exc}"
        (out / "error.log").write_text(summary["error"] + "\n", encoding="utf-8")

    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(summary["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
