#!/usr/bin/env python3
"""Build the E11 integration sample as two deterministic commits."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().parent / "sample"
DEFAULT_OUTPUT = ROOT / "work" / "e11" / "sample" / "repo"
AUTHOR_NAME = "B07 E11"
AUTHOR_EMAIL = "b07-e11@example.invalid"
DATE = "2026-11-26T00:00:00Z"


def run(*args: str, cwd: Path) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "command failed")
    return result.stdout.strip()


def commit(repo: Path, message: str) -> str:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": AUTHOR_NAME,
            "GIT_AUTHOR_EMAIL": AUTHOR_EMAIL,
            "GIT_COMMITTER_NAME": AUTHOR_NAME,
            "GIT_COMMITTER_EMAIL": AUTHOR_EMAIL,
            "GIT_AUTHOR_DATE": DATE,
            "GIT_COMMITTER_DATE": DATE,
        }
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True, env=env)
    return run("git", "rev-parse", "HEAD", cwd=repo)


def build(output: Path) -> dict[str, str]:
    if output.exists():
        shutil.rmtree(output)
    baseline = SOURCE / "baseline"
    target = SOURCE / "target"
    if not baseline.is_dir() or not target.is_dir():
        raise RuntimeError("sample baseline or target directory is missing")
    shutil.copytree(baseline, output)
    run("git", "init", "-q", cwd=output)
    run("git", "config", "core.autocrlf", "false", cwd=output)
    baseline_sha = commit(output, "baseline")
    for source in target.rglob("*"):
        if source.is_file():
            destination = output / source.relative_to(target)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    target_sha = commit(output, "target")
    return {"baseline_commit": baseline_sha, "target_commit": target_sha}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the E11 sample repository.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=Path(__file__).resolve().parent / "sample.json")
    args = parser.parse_args(argv)
    try:
        result = build(args.output.resolve())
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"prepare_sample: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.manifest.is_file():
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if manifest.get("baseline_commit") != result["baseline_commit"] or manifest.get("target_commit") != result[
            "target_commit"]:
            print("prepare_sample: commits do not match manifest", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
