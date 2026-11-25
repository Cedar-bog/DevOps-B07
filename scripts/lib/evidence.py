"""Shared helpers for stage evidence and gate scripts."""

from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def new_run_dir(base: Path) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    run = base / stamp()
    suffix = 1
    while run.exists():
        run = base / f"{stamp()}-{suffix}"
        suffix += 1
    run.mkdir()
    return run


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "git", "-C", str(root),
            "-c", "user.name=E11 Evidence",
            "-c", "user.email=e11@example.invalid",
            "-c", "commit.gpgsign=false",
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def tool_version(command: str) -> str:
    try:
        result = subprocess.run(
            [command, "--version"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    line = (result.stdout or result.stderr).splitlines()
    return line[0].strip() if line else "unavailable"
