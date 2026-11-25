#!/usr/bin/env python3
"""Validate the E11 sample, fixed report, and request files locally."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SERVICES = ROOT / "services"
sys.path.insert(0, str(SERVICES / "md-fixer"))
sys.path.insert(0, str(SERVICES / "draft"))

from mdfixer.guard import read_source  # noqa: E402
from mdfixer.makefile import parse_makefile  # noqa: E402
from mdfixer.report import validate_report  # noqa: E402


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(*args: str, cwd: Path) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "command failed")
    return result.stdout.strip()


def extract(repo: Path, commit: str, destination: Path) -> None:
    archive = destination / "source.tar"
    with archive.open("wb") as handle:
        result = subprocess.run(
            ["git", "-C", str(repo), "archive", commit],
            stdout=handle,
            stderr=subprocess.PIPE,
            check=False,
        )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip() or "git archive failed")
    with tarfile.open(archive) as tar:
        tar.extractall(destination)
    archive.unlink()


def check_program(project: Path, expected: str) -> None:
    run("make", "app", cwd=project)
    result = subprocess.run(["./app"], cwd=project, capture_output=True, text=True, check=False)
    if result.returncode != 0 or result.stdout.rstrip("\r\n") != expected:
        raise RuntimeError(f"program output {result.stdout!r} does not match {expected!r}")


def main() -> int:
    manifest = load(HERE / "sample.json")
    report = load(HERE / "fixed-md-report.json")
    draft = load(HERE / "draft-request.json")
    repair = load(HERE / "repair-request.json")
    digest = (HERE / "fixed-md-report.sha256").read_text(encoding="utf-8").strip()
    raw = (HERE / "fixed-md-report.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise RuntimeError("fixed report SHA256 does not match")

    with tempfile.TemporaryDirectory(prefix="e11-check-") as temporary:
        temporary = Path(temporary)
        repo = temporary / "repo"
        result = subprocess.run(
            [
                sys.executable,
                str(HERE / "prepare_sample.py"),
                "--output",
                str(repo),
                "--manifest",
                str(HERE / "sample.json"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "prepare_sample failed")

        baseline = temporary / "baseline"
        baseline.mkdir()
        extract(repo, manifest["baseline_commit"], baseline)
        check_program(baseline, manifest["expected_output"])
        check_program(repo, manifest["expected_output"])

        source = read_source(repo, manifest["makefile"], commit=manifest["target_commit"])
        document = parse_makefile(source.text)
        validated = validate_report(report, source, document)
        missing = {(item.target, item.dependency) for item in validated.missing}
        expected_missing = {tuple(item) for item in manifest["missing"]}
        if missing != expected_missing:
            raise RuntimeError(f"MISSING mismatch: {sorted(missing)}")
        if set(validated.ignored) != {"REDUNDANT-main-o-unused-h"}:
            raise RuntimeError("REDUNDANT finding mismatch")

        if draft["input"]["repository"]["commit"] != manifest["target_commit"]:
            raise RuntimeError("DRAFT request commit mismatch")
        if repair["input"]["commit"] != manifest["target_commit"]:
            raise RuntimeError("REPAIR request commit mismatch")
        if repair["input"]["md_report_sha256"] != digest:
            raise RuntimeError("REPAIR request hash mismatch")

    print("sample-ok")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, KeyError) as exc:
        print(f"check_sample: {exc}", file=sys.stderr)
        raise SystemExit(1)
