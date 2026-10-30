"""Run an image with resource limits, compare stdout, and clean up the container."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from .docker import CommandResult, DockerError, Runner, default_runner

DEFAULT_VERIFY_TIMEOUT = 120.0
DEFAULT_CPU = "2"
DEFAULT_MEMORY = "1g"
DEFAULT_PIDS = 256
CLEANUP_TIMEOUT = 60.0


@dataclass(frozen=True)
class VerifyResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    expected_output: str
    actual_output: str
    output_matches: bool
    timed_out: bool
    container_id: str | None
    container_name: str
    container_removed: bool
    duration_s: float

    def ok(self) -> bool:
        return not self.timed_out and self.exit_code == 0 and self.output_matches

    @property
    def log(self) -> str:
        return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", self.stdout + self.stderr).replace("\r", "")


def normalize_output(text: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r", "").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def outputs_match(actual: str, expected: str) -> bool:
    return normalize_output(actual) == normalize_output(expected)


def remove_container(container_name: str, *, docker: str = "docker", runner: Runner | None = None) -> bool:
    try:
        result = (runner or default_runner)(
            [docker, "rm", "-f", container_name], Path.cwd(), CLEANUP_TIMEOUT,
        )
        return result.exit_code == 0 and not result.timed_out
    except OSError:
        return False


def run_image(
    image_id: str, *, expected_output: str, container_name: str,
    cidfile: Path | str | None = None, docker: str = "docker", network: str = "none",
    cpus: str = DEFAULT_CPU, memory: str = DEFAULT_MEMORY, pids_limit: int = DEFAULT_PIDS,
    timeout: float = DEFAULT_VERIFY_TIMEOUT, runner: Runner | None = None,
) -> VerifyResult:
    if not image_id or not image_id.startswith("sha256:"):
        raise DockerError("verification requires a sha256: image ID")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("verify timeout must be finite and greater than zero")
    if not container_name or pids_limit <= 0:
        raise ValueError("container name and a positive PID limit are required")
    invoke = runner or default_runner
    with TemporaryDirectory(prefix="draft-verify-") as temporary:
        target = Path(cidfile).expanduser().resolve() if cidfile else Path(temporary) / "cid"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise DockerError(f"container ID file already exists: {target}")
        command = [
            docker, "run", "--name", container_name, "--network", network,
            "--cpus", cpus, "--memory", memory, "--pids-limit", str(pids_limit),
            "--cidfile", str(target), image_id,
        ]
        try:
            result: CommandResult = invoke(command, target.parent, timeout)
        except OSError as exc:
            raise DockerError(f"cannot execute {docker}: {exc}") from exc
        finally:
            removed = remove_container(container_name, docker=docker, runner=runner)
        container_id = target.read_text(encoding="utf-8").strip() if target.is_file() else None
    return VerifyResult(
        command, result.exit_code, result.stdout, result.stderr, expected_output,
        result.stdout, outputs_match(result.stdout, expected_output), result.timed_out,
        container_id or None, container_name, removed, result.duration_s,
    )
