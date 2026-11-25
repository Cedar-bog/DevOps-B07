"""Rejection rollback: failed transactions leave the source bytes intact."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .accept import write_atomic
from .guard import project_file


@dataclass(frozen=True)
class RollbackResult:
    ok: bool
    source_unchanged: bool
    restored: bool
    sha256: str
    error: str | None = None

    def to_payload(self) -> dict:
        return {
            "ok": self.ok,
            "source_unchanged": self.source_unchanged,
            "restored": self.restored,
            "sha256": self.sha256,
            "error": self.error,
        }


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_source_bytes(project_root: Path | str, makefile: str = "Makefile") -> tuple[Path, bytes]:
    root = Path(project_root).resolve()
    path = project_file(root, makefile)
    try:
        return path, path.read_bytes()
    except OSError as exc:
        raise OSError(f"cannot read Makefile: {exc}") from exc


def source_unchanged(
    project_root: Path | str, original: bytes, makefile: str = "Makefile"
) -> bool:
    try:
        _, current = read_source_bytes(project_root, makefile)
    except OSError:
        return False
    return current == original


def rollback_source(
    project_root: Path | str, original: bytes, makefile: str = "Makefile"
) -> RollbackResult:
    """Restore the original bytes if a failed transaction touched the source."""
    path, current = read_source_bytes(project_root, makefile)
    expected = sha256_bytes(original)
    if current == original:
        return RollbackResult(True, True, False, expected)
    try:
        write_atomic(path, original)
        _, restored = read_source_bytes(project_root, makefile)
    except OSError as exc:
        return RollbackResult(False, False, False, expected, str(exc))
    if restored != original:
        return RollbackResult(False, False, True, expected, "restored bytes do not match")
    return RollbackResult(True, True, True, expected)
