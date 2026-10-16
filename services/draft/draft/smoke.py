"""Smoke check for the DRAFT service.

Builds ``fixtures/draft/Dockerfile.broken`` with Docker and expects a
locatable failure (nonzero exit code plus the ``make: not found``
signature). The check only confirms the environment can run a build and
capture its failure log; it does not evaluate the DRAFT algorithm.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

SERVICE = "draft"
SCHEMA_VERSION = "1.0"
DOCKERFILE_NAME = "Dockerfile.broken"
REQUIRED_FILES = (DOCKERFILE_NAME, "main.c", "Makefile")
EXPECTED_SIGNATURE = "make: not found"
LOG_TAIL_LIMIT = 4000
DEFAULT_TIMEOUT = 600.0
CLEANUP_TIMEOUT = 120.0

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

Runner = Callable[[Sequence[str], Path, float], "tuple[int, str]"]


class SmokeError(Exception):
    """The smoke check could not be executed."""


@dataclass
class SmokeResult:
    run_id: str
    dockerfile: str
    image_tag: str
    exit_code: int
    signature: str
    signature_found: bool
    ok: bool
    log_tail: str
    started_at: str
    finished_at: str
    duration_s: float

    def to_payload(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "service": SERVICE,
            "run_id": self.run_id,
            "dockerfile": self.dockerfile,
            "image_tag": self.image_tag,
            "expected": {"exit_code": "nonzero", "log_contains": self.signature},
            "observed": {
                "exit_code": self.exit_code,
                "log_has_signature": self.signature_found,
            },
            "ok": self.ok,
            "log_tail": self.log_tail,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": self.duration_s,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _default_runner(command: Sequence[str], cwd: Path, timeout: float) -> tuple[int, str]:
    proc = subprocess.run(
        list(command),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _invoke(
    runner: Runner,
    command: Sequence[str],
    cwd: Path,
    timeout: float,
) -> tuple[int, str]:
    try:
        return runner(command, cwd, timeout)
    except FileNotFoundError as exc:
        raise SmokeError(f"executable not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        return -1, f"timeout after {exc.timeout}s"


def _clean(text: str) -> str:
    return _ANSI.sub("", text).replace("\r", "")


def run_smoke(
    fixture_dir: Path | str,
    *,
    docker: str = "docker",
    image_tag: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    runner: Runner | None = None,
) -> SmokeResult:
    fixture = Path(fixture_dir).expanduser()
    if not fixture.is_dir():
        raise SmokeError(f"fixture directory not found: {fixture}")
    missing = [name for name in REQUIRED_FILES if not (fixture / name).is_file()]
    if missing:
        raise SmokeError(f"fixture files missing: {', '.join(missing)}")

    invoke = runner or _default_runner
    run_id = _new_run_id()
    tag = image_tag or f"e4-draft-smoke:{run_id}"

    started_at = _now_iso()
    started = time.monotonic()
    exit_code, output = _invoke(
        invoke,
        [docker, "build", "--no-cache", "-f", DOCKERFILE_NAME, "-t", tag, "."],
        fixture,
        timeout,
    )
    log = _clean(output)
    signature_found = EXPECTED_SIGNATURE in log
    ok = exit_code != 0 and signature_found

    try:
        _invoke(invoke, [docker, "rmi", "-f", tag], fixture, CLEANUP_TIMEOUT)
    except Exception:
        pass

    return SmokeResult(
        run_id=run_id,
        dockerfile=DOCKERFILE_NAME,
        image_tag=tag,
        exit_code=exit_code,
        signature=EXPECTED_SIGNATURE,
        signature_found=signature_found,
        ok=ok,
        log_tail=log[-LOG_TAIL_LIMIT:],
        started_at=started_at,
        finished_at=_now_iso(),
        duration_s=round(time.monotonic() - started, 3),
    )


def write_result(result: SmokeResult, path: Path | str) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result.to_payload(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target
