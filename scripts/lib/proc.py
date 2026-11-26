"""Small process and formatting helpers shared by operator scripts."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

DEFAULT_TIMEOUT_S = 15.0


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tail(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def run(
        command: Sequence[str],
        *,
        cwd: Path,
        timeout: float = DEFAULT_TIMEOUT_S,
        strip: bool = False,
) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            list(command),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError:
        return 127, "", f"executable not found: {command[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout:g}s"
    except OSError as exc:
        return 126, "", str(exc)
    out = proc.stdout or ""
    err = proc.stderr or ""
    if strip:
        out = out.strip()
        err = err.strip()
    return proc.returncode, out, err


def git(
        repo: Path,
        *args: str,
        timeout: float = DEFAULT_TIMEOUT_S,
        strip: bool = False,
) -> tuple[int, str, str]:
    return run(["git", *args], cwd=repo, timeout=timeout, strip=strip)
