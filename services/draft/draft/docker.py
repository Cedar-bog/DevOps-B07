"""Docker builds with complete logs and an injectable command runner."""

from __future__ import annotations

import math
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

DEFAULT_BUILD_TIMEOUT = 900.0
LOG_TAIL_LIMIT = 20000
CLEANUP_TIMEOUT = 60.0
FAILURE_CLASSES = ("NONE", "MISSING_MAKE", "APT_ERROR", "NETWORK", "TIMEOUT", "OTHER")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class DockerError(Exception):
    """Docker could not be invoked or the build inputs are missing."""


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False


Runner = Callable[[list[str], Path, float], CommandResult]


def _clean(text: str) -> str:
    return _ANSI.sub("", text).replace("\r", "")


def _utc(now=None) -> datetime:
    value = now() if callable(now) else now
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def default_runner(command: list[str], cwd: Path, timeout: float) -> CommandResult:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("command timeout must be finite and greater than zero")
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command, cwd=cwd, capture_output=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
        return CommandResult(proc.returncode, proc.stdout, proc.stderr, time.monotonic() - started)
    except subprocess.TimeoutExpired as exc:
        def decode(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
        return CommandResult(
            -1, decode(exc.stdout), decode(exc.stderr) + f"\nTimeout after {timeout:g}s\n",
            time.monotonic() - started, True,
        )


def _invoke(runner: Runner, command: list[str], cwd: Path, timeout: float) -> CommandResult:
    try:
        return runner(command, cwd, timeout)
    except OSError as exc:
        raise DockerError(f"cannot execute {command[0]}: {exc}") from exc


@dataclass(frozen=True)
class BuildResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    log: str
    image_tag: str
    image_id: str | None
    failed_step: str | None
    failure_class: str
    timed_out: bool
    started_at: str
    finished_at: str
    duration_s: float

    def ok(self) -> bool:
        return not self.timed_out and self.exit_code == 0 and self.image_id is not None


def classify_failure(log: str, exit_code: int, timed_out: bool) -> str:
    if timed_out:
        return "TIMEOUT"
    if exit_code == 0:
        return "NONE"
    lower = log.lower()
    if "make: not found" in lower or "make: command not found" in lower:
        return "MISSING_MAKE"
    if any(s in lower for s in ("unable to locate package", "e: ")) or re.search(
        r"apt-get[^\n]*returned a non-zero code", lower
    ):
        return "APT_ERROR"
    if any(s in lower for s in (
        "temporary failure resolving", "i/o timeout", "connection reset", "tls handshake timeout",
    )):
        return "NETWORK"
    return "OTHER"


def locate_failed_step(log: str) -> str | None:
    steps = re.findall(r"^#\d+\s+\[[^\]\n]*\d+/\d+\]\s+(.+)$", _clean(log), re.MULTILINE)
    return steps[-1].strip() if steps else None


def image_id_of(tag: str, *, docker: str = "docker", runner: Runner | None = None) -> str | None:
    result = _invoke(
        runner or default_runner, [docker, "image", "inspect", "--format", "{{.Id}}", tag],
        Path.cwd(), CLEANUP_TIMEOUT,
    )
    value = result.stdout.strip()
    return value if result.exit_code == 0 and not result.timed_out and value.startswith("sha256:") else None


def remove_image(tag: str, *, docker: str = "docker", runner: Runner | None = None) -> bool:
    try:
        result = _invoke(runner or default_runner, [docker, "rmi", "-f", tag], Path.cwd(), CLEANUP_TIMEOUT)
        return result.exit_code == 0 and not result.timed_out
    except DockerError:
        return False


def build_image(
    context_dir: Path | str, *, dockerfile: str = "Dockerfile", docker: str = "docker",
    tag: str | None = None, timeout: float = DEFAULT_BUILD_TIMEOUT, no_cache: bool = False,
    runner: Runner | None = None, now=None,
) -> BuildResult:
    context = Path(context_dir).expanduser().resolve()
    if not context.is_dir() or not (context / dockerfile).is_file():
        raise DockerError(f"build context or Dockerfile not found: {context / dockerfile}")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("build timeout must be finite and greater than zero")
    started = _utc(now)
    image_tag = tag or f"e6-draft-iter:{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    command = [docker, "build", "--progress=plain", "-f", dockerfile, "-t", image_tag, "."]
    if no_cache:
        command.append("--no-cache")
    result = _invoke(runner or default_runner, command, context, timeout)
    log = _clean(result.stdout + result.stderr)
    image_id = None
    if result.exit_code == 0 and not result.timed_out:
        image_id = image_id_of(image_tag, docker=docker, runner=runner)
        if image_id is None:
            log += "\nImage inspection failed: no image ID returned.\n"
    return BuildResult(
        command, result.exit_code, result.stdout, result.stderr, log, image_tag, image_id,
        locate_failed_step(log) if not image_id else None,
        "OTHER" if result.exit_code == 0 and not image_id and not result.timed_out else
        classify_failure(log, result.exit_code, result.timed_out),
        result.timed_out, started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        _utc(now).strftime("%Y-%m-%dT%H:%M:%SZ"), result.duration_s,
    )
