"""E10 sandbox preparation and candidate application.

The source project is never modified here. A transaction copies the project,
checks the candidate with ``git apply --check``, then applies it to the copy.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .guard import RepairError, project_file


@dataclass(frozen=True)
class ApplyResult:
    ok: bool
    check_returncode: int | None
    apply_returncode: int | None
    command: tuple[str, ...]
    stdout: str = ""
    stderr: str = ""

    @property
    def error(self) -> str | None:
        if self.ok:
            return None
        detail = (self.stderr or self.stdout).strip()
        return detail or "git apply failed without output"

    def to_payload(self) -> dict:
        return {
            "ok": self.ok,
            "check_returncode": self.check_returncode,
            "apply_returncode": self.apply_returncode,
            "command": list(self.command),
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def _sensitive(path: Path) -> bool:
    return any(
        part.lower() == ".env" or part.lower().startswith(".env.")
        for part in path.resolve().parts
    )


def _remove_readonly(function, path, exc_info) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        function(path)
    except OSError:
        pass


def create_sandbox(
    project_root: Path | str,
    destination: Path | str | None = None,
    *,
    ignore: tuple[str, ...] = (
        ".venv", "__pycache__", ".pytest_cache", "work", "evidence", ".env", ".env.*",
    ),
) -> Path:
    """Copy a project into an independent sandbox and return its path."""
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise RepairError("MISSING_INPUT", "project directory not found")
    if destination is None:
        target = Path(tempfile.mkdtemp(prefix="mdfixer-e10-")).resolve()
    else:
        target = Path(destination).expanduser().resolve()
        if _sensitive(target):
            raise RepairError("UNSAFE_PATH", "sandbox must not be a sensitive path")
        if target == root or target.is_relative_to(root) or root.is_relative_to(target):
            raise RepairError("UNSAFE_PATH", "sandbox must be outside the source project")
        if target.exists() and any(target.iterdir()):
            raise RepairError("SANDBOX_ERROR", "sandbox destination must be empty")
        target.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(
            root,
            target,
            symlinks=True,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(*ignore),
        )
    except OSError as exc:
        removed = target
        if destination is None:
            shutil.rmtree(removed, onerror=_remove_readonly)
        raise RepairError("SANDBOX_ERROR", "cannot copy project into sandbox") from exc
    if not (target / ".git").exists():
        _git(target, "init", "-q")
    return target


def remove_sandbox(path: Path | str) -> None:
    target = Path(path)
    if target.exists():
        shutil.rmtree(target, onerror=_remove_readonly)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), *args],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RepairError("SANDBOX_ERROR", "cannot run git in sandbox") from exc


def apply_candidate(sandbox: Path | str, git_patch: str) -> ApplyResult:
    """Check and apply a candidate patch inside a sandbox."""
    root = Path(sandbox).resolve()
    if not root.is_dir():
        raise RepairError("MISSING_INPUT", "sandbox directory not found")
    if not isinstance(git_patch, str):
        raise ValueError("git_patch must be text")
    command = ("git", "-C", str(root), "apply", "--check", "--whitespace=nowarn", "-")
    try:
        check = subprocess.run(
            command,
            input=git_patch.encode("utf-8"),
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RepairError("SANDBOX_ERROR", "cannot run git apply --check") from exc
    check_out = check.stdout.decode("utf-8", errors="replace")
    check_err = check.stderr.decode("utf-8", errors="replace")
    if check.returncode:
        return ApplyResult(
            False, check.returncode, None, command, check_out, check_err
        )
    apply_command = ("git", "-C", str(root), "apply", "--whitespace=nowarn", "-")
    try:
        applied = subprocess.run(
            apply_command,
            input=git_patch.encode("utf-8"),
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RepairError("SANDBOX_ERROR", "cannot run git apply") from exc
    return ApplyResult(
        applied.returncode == 0,
        check.returncode,
        applied.returncode,
        apply_command,
        applied.stdout.decode("utf-8", errors="replace"),
        applied.stderr.decode("utf-8", errors="replace"),
    )


def makefile_bytes(project_root: Path | str, makefile: str = "Makefile") -> bytes:
    """Read the current Makefile bytes without mutating the project."""
    root = Path(project_root).resolve()
    path = project_file(root, makefile)
    try:
        return path.read_bytes()
    except OSError as exc:
        raise RepairError("MISSING_INPUT", "cannot read Makefile") from exc
