#!/usr/bin/env python3
"""Generate E9-009 evidence for the four MDFixer declaration styles."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "md-fixer"
SERVICE_PATH = ROOT / "services" / "md-fixer"
DRAFT_PATH = ROOT / "services" / "draft"
STYLES = ("target", "macro", "hybrid", "implicit")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def _new_run_dir() -> Path:
    base = ROOT / "evidence" / "e9"
    base.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    run = base / stamp
    suffix = 1
    while run.exists():
        run = base / f"{stamp}-{suffix}"
        suffix += 1
    run.mkdir()
    return run


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(command: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return _run(
        ["git", "-C", str(root), "-c", "user.name=E9 Evidence",
         "-c", "user.email=e9@example.invalid", "-c", "commit.gpgsign=false", *args],
        root,
        os.environ.copy(),
    )


def _rule(lines: list[str], style: str) -> tuple[int, str]:
    for number, line in enumerate(lines, 1):
        if style == "implicit" and line.startswith("%.o:"):
            return number, line
        if style != "implicit" and line.startswith("main.o:"):
            return number, line
    raise RuntimeError(f"no rule found for style {style}")


def _report(style: str, commit: str, line: int, declaration: str) -> dict:
    return {
        "schema_version": "1.0",
        "repository": {
            "url": "https://github.com/Cedar-bog/DevOps-B07",
            "commit": commit,
        },
        "configuration_id": f"e9-{style}-MODE0",
        "producer_job_id": f"job-e9-{style}",
        "findings": [
            {
                "finding_id": f"MISSING-main-o-config-h-{style}",
                "type": "MISSING",
                "target": "main.o",
                "dependency": "config.h",
                "commit": commit,
                "position": {
                    "file": "Makefile",
                    "line": line,
                    "declaration": declaration,
                },
                "detector": "INSTRUCTOR_ORACLE",
            }
        ],
    }


def run_style(style: str, run_dir: Path, env: dict[str, str]) -> dict:
    style_dir = run_dir / style
    style_dir.mkdir()
    project = style_dir / "project"
    project.mkdir()
    for name in ("main.c", "config.h", "unused.h"):
        shutil.copyfile(FIXTURE / name, project / name)
    makefile_before = FIXTURE / "styles" / style / "Makefile.before"
    shutil.copyfile(makefile_before, project / "Makefile")
    reference = FIXTURE / "styles" / style / "reference.patch"
    shutil.copyfile(reference, style_dir / "reference.patch")

    lines = (project / "Makefile").read_text(encoding="utf-8").splitlines()
    line, declaration = _rule(lines, style)

    git_init = _git(project, "init", "-q")
    git_add = _git(project, "add", ".")
    git_commit = _git(project, "commit", "-qm", "e9 input")
    if any(item.returncode for item in (git_init, git_add, git_commit)):
        raise RuntimeError(f"cannot prepare {style} project")
    commit = _git(project, "rev-parse", "HEAD").stdout.strip()

    report = _report(style, commit, line, declaration)
    report_path = style_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    before = {name: _sha(project / name) for name in ("Makefile", "main.c", "config.h", "unused.h")}
    command = [
        sys.executable, "-m", "mdfixer.cli", "generate",
        "--project", str(project),
        "--report", str(report_path),
        "--json",
        "--patch-out", str(style_dir / "candidate.patch"),
        "--plan-out", str(style_dir / "plan.json"),
    ]
    command_text = " ".join(command)
    (style_dir / "command.txt").write_text(
        "cwd: " + str(ROOT) + "\n" + command_text + "\n", encoding="utf-8"
    )
    result = _run(command, ROOT, env)
    (style_dir / "stdout.json").write_text(result.stdout, encoding="utf-8")
    (style_dir / "stderr.log").write_text(result.stderr, encoding="utf-8")
    (style_dir / "exit-code.txt").write_text(f"{result.returncode}\n", encoding="utf-8")

    candidate = style_dir / "candidate.patch"
    plan = style_dir / "plan.json"
    payload = json.loads(result.stdout) if result.stdout.strip().startswith("{") else None

    reference_lines = reference.read_text(encoding="utf-8").splitlines(keepends=True)
    expected = "diff --git a/Makefile b/Makefile\n--- a/Makefile\n+++ b/Makefile\n" + "".join(reference_lines[2:])
    generated = candidate.read_text(encoding="utf-8") if candidate.is_file() else ""
    reference_matches = generated == expected

    apply_check = _git(project, "apply", "--check", str(candidate)) if candidate.is_file() else None
    source_unchanged = before == {name: _sha(project / name) for name in before}

    verification = {
        "style": style,
        "exit_code": result.returncode,
        "candidate_exists": candidate.is_file(),
        "plan_exists": plan.is_file(),
        "reference_matches": reference_matches,
        "git_apply_check": bool(apply_check and apply_check.returncode == 0),
        "source_unchanged": source_unchanged,
        "candidate_sha256": _sha(candidate) if candidate.is_file() else None,
        "plan_change_count": len(payload["plan"]["changes"]) if payload else None,
    }
    (style_dir / "verification.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return verification


def main() -> int:
    run_dir = _new_run_dir()
    env = os.environ.copy()
    paths = [str(SERVICE_PATH), str(DRAFT_PATH)]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)

    summary = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip(),
        "python": sys.version,
        "platform": platform.platform(),
        "styles": {},
    }
    (run_dir / "command.txt").write_text(
        "cwd: " + str(ROOT) + "\n"
        + "PYTHONPATH=" + env["PYTHONPATH"] + "\n"
        + "python -m mdfixer.cli generate --project <style>/project --report <style>/report.json "
          "--json --patch-out <style>/candidate.patch --plan-out <style>/plan.json\n",
        encoding="utf-8",
    )

    try:
        for style in STYLES:
            summary["styles"][style] = run_style(style, run_dir, env)
    finally:
        summary["all_passed"] = all(
            item["exit_code"] == 0
            and item["candidate_exists"]
            and item["plan_exists"]
            and item["reference_matches"]
            and item["git_apply_check"]
            and item["source_unchanged"]
            for item in summary["styles"].values()
        ) and len(summary["styles"]) == len(STYLES)
        (run_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    print(run_dir)
    print("all_passed:", summary["all_passed"])
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
