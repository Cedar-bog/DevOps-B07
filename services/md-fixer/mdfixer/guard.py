"""E9 input guards. All filesystem and Git operations in this module are reads."""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath


class RepairError(ValueError):
    """A rejected candidate/input, with a stable machine-readable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def relative_path(value: object) -> str:
    """Accept literal project paths, never Make expressions or shell syntax."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise RepairError("UNSAFE_PATH", "expected a nonempty relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute() or PureWindowsPath(value).drive
        or "\\" in value or ".." in path.parts
        or any(p.lower() in {".git", ".env"} or p.lower().startswith(".env.") for p in path.parts)
        or not re.fullmatch(r"[A-Za-z0-9_./+@-]+", value)
        or value.startswith("-") or any(p.startswith("-") for p in path.parts)
    ):
        raise RepairError("UNSAFE_PATH", "path must be a literal path inside the project")
    return path.as_posix()


def project_file(root: Path, value: object) -> Path:
    name = relative_path(value)
    try:
        resolved = (root / name).resolve()
    except (OSError, RuntimeError) as exc:
        raise RepairError("UNSAFE_PATH", "cannot safely resolve the project path") from exc
    if not resolved.is_relative_to(root.resolve()):
        raise RepairError("UNSAFE_PATH", "path escapes the project through a link")
    if not resolved.is_file():
        raise RepairError("MISSING_INPUT", f"project file not found: {name}")
    return resolved


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), *args],
            capture_output=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RepairError("MISSING_INPUT", "cannot read project Git identity") from exc
    if result.returncode:
        raise RepairError("MISSING_INPUT", "project must have a committed Git HEAD and Makefile")
    try:
        return result.stdout.decode("utf-8")
    except UnicodeError as exc:
        raise RepairError("MISSING_INPUT", "Git identity and Makefile must be UTF-8") from exc


def tracked_path(root: Path, path: Path) -> bool:
    """Return whether a resolved project file is tracked at HEAD."""
    try:
        git_root = Path(_git(root, "rev-parse", "--show-toplevel").strip()).resolve()
        name = path.resolve().relative_to(git_root).as_posix()
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), "ls-files", "--error-unmatch", "--", name],
            capture_output=True, timeout=10, check=False,
        )
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


@dataclass(frozen=True)
class Source:
    root: Path
    path: Path
    name: str
    commit: str
    text: str
    sha256: str


def read_source(
    project_root: Path | str, makefile: str = "Makefile", *, commit: str | None = None,
) -> Source:
    """Require a tracked, unchanged Makefile at the requested project HEAD.

    A caller may select a subdirectory as its project root. Report paths must
    then be relative to that directory, not to an unrelated checkout.
    """
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise RepairError("MISSING_INPUT", "project directory not found")
    name = relative_path(makefile)
    path = project_file(root, name)
    if path != root / name:
        raise RepairError("UNSAFE_PATH", "Makefile paths through links are unsupported")
    head = _git(root, "rev-parse", "--verify", "HEAD").strip()
    if commit is not None and commit != head:
        raise RepairError("STALE_REPORT", "requested commit does not match project HEAD")
    if _git(root, "diff", "--name-only", "HEAD", "--", ".").strip():
        raise RepairError("STALE_REPORT", "tracked project inputs differ from the reported commit")
    git_root = Path(_git(root, "rev-parse", "--show-toplevel").strip()).resolve()
    if not path.is_relative_to(git_root):
        raise RepairError("UNSAFE_PATH", "Makefile is outside the checkout")
    tracked_name = path.relative_to(git_root).as_posix()
    committed = _git(root, "show", f"HEAD:{tracked_name}")
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise RepairError("MISSING_INPUT", "Makefile must be readable UTF-8") from exc
    if text.replace("\r\n", "\n") != committed.replace("\r\n", "\n"):
        raise RepairError("STALE_REPORT", "Makefile differs from the reported commit")
    if not text or not text.endswith("\n") or "\x00" in text:
        raise RepairError("UNSUPPORTED_TARGET", "Makefile must be nonempty text with a final newline")
    if "\r\n" in text and "\n" in text.replace("\r\n", ""):
        raise RepairError("UNSUPPORTED_TARGET", "mixed Makefile newline conventions are unsupported")
    return Source(root, path, name, head, text, hashlib.sha256(raw).hexdigest())
