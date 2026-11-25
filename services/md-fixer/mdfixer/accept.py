"""Write-back of a candidate after every sandbox check passed."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .guard import RepairError, read_source


@dataclass(frozen=True)
class AcceptResult:
    accepted: bool
    path: str
    previous_sha256: str
    new_sha256: str

    def to_payload(self) -> dict:
        return {
            "accepted": self.accepted,
            "path": self.path,
            "previous_sha256": self.previous_sha256,
            "new_sha256": self.new_sha256,
        }


def write_atomic(path: Path | str, data: bytes) -> Path:
    """Replace a file only after the complete new content is on disk."""
    target = Path(path)
    mode = target.stat().st_mode & 0o777 if target.exists() else 0o644
    temporary = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=target.parent, prefix=f".{target.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        os.chmod(target, mode)
    except OSError as exc:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        raise RepairError("WRITE_FAILED", "cannot write accepted candidate") from exc
    return target


def accept_candidate(
    project_root: Path | str,
    candidate_text: str,
    *,
    makefile: str = "Makefile",
    expected_sha256: str | None = None,
) -> AcceptResult:
    """Write back only when the source still matches the generated snapshot."""
    if not isinstance(candidate_text, str) or not candidate_text:
        raise RepairError("INVALID_INPUT", "accepted candidate must be nonempty text")
    source = read_source(project_root, makefile)
    if expected_sha256 is not None and source.sha256 != expected_sha256:
        raise RepairError("STALE_REPORT", "source Makefile changed before write-back")
    data = candidate_text.encode("utf-8")
    target = write_atomic(source.path, data)
    new_sha = hashlib.sha256(data).hexdigest()
    if hashlib.sha256(target.read_bytes()).hexdigest() != new_sha:
        raise RepairError("WRITE_FAILED", "written Makefile does not match the candidate")
    return AcceptResult(True, str(target), source.sha256, new_sha)
