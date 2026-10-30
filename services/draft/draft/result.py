"""Isolated run directories and atomic evidence summaries."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

RESULT_SCHEMA_VERSION = "1.0"
RESULT_FILENAME = "result.json"


@dataclass(frozen=True)
class RunPaths:
    root: Path
    project: Path

    def iter_dir(self, iteration: int) -> Path:
        if iteration < 1:
            raise ValueError("iteration must be positive")
        target = self.root / f"iter-{iteration:02d}"
        target.mkdir(parents=True, exist_ok=True)
        return target


@dataclass(frozen=True)
class RoundRecord:
    iteration: int
    dockerfile_sha256: str
    selected: dict
    rejected: list[dict]
    build: dict
    verify: dict | None
    evidence: dict


def new_run_dir(work_root: Path | str, *, now=None) -> RunPaths:
    parent = Path(work_root).expanduser().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    value = now() if callable(now) else now
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    stamp = value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    suffix = 0
    while True:
        root = parent / (stamp + (f"-{suffix}" if suffix else ""))
        try:
            root.mkdir()
            return RunPaths(root, root / "project")
        except FileExistsError:
            suffix += 1


def prepare_project(paths: RunPaths, fixture_dir: Path | str) -> Path:
    fixture = Path(fixture_dir).expanduser().resolve()
    if paths.root.resolve().is_relative_to(fixture):
        raise ValueError("output directory must be outside the fixture directory")
    shutil.copytree(fixture, paths.project, ignore=shutil.ignore_patterns(".git", ".env", ".env.*", ".venv", "__pycache__"))
    return paths.project


def _write_json(target: Path, payload: dict) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def write_round(paths: RunPaths, round_record: RoundRecord) -> Path:
    return _write_json(paths.iter_dir(round_record.iteration) / "round.json", asdict(round_record))


def write_result(paths: RunPaths, payload: dict) -> Path:
    return _write_json(paths.root / RESULT_FILENAME, payload)
