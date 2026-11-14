"""E9 candidate handoff records; acceptance/recheck belong to E10."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping


@dataclass(frozen=True)
class Rejection:
    """A candidate-level rejection record.

    Internal codes such as STALE_REPORT and UNSAFE_PATH must stay here and must
    not be written to ``job.error``, whose enum is fixed by the shared contract.
    """

    code: str
    message: str
    patch: str = ""

    def to_payload(self) -> dict:
        return {"patch": self.patch, "reason": f"{self.code}: {self.message}"}


def rejection_from_error(error: Exception, *, patch: str = "") -> Rejection:
    return Rejection(
        code=getattr(error, "code", "INVALID_INPUT"),
        message=str(error),
        patch=patch,
    )


@dataclass(frozen=True)
class Change:
    finding_id: str
    target: str
    dependency: str
    style: str
    strategy: str
    line: int
    reason: str
    variable: str | None = None
    affected_targets: tuple[str, ...] = ()
    changed: bool = True


@dataclass(frozen=True)
class RepairPlan:
    commit: str
    configuration_id: str
    makefile: str
    source_sha256: str
    changes: tuple[Change, ...]
    ignored_findings: tuple[str, ...]

    def to_payload(self) -> dict:
        return {
            "schema_version": "1.0",
            "commit": self.commit,
            "configuration_id": self.configuration_id,
            "makefile": self.makefile,
            "source_sha256": self.source_sha256,
            "changes": [asdict(change) for change in self.changes],
            "ignored_findings": list(self.ignored_findings),
        }


@dataclass(frozen=True)
class PatchResult:
    git_patch: str
    candidate_text: str
    plan: RepairPlan
    rejected_candidates: tuple[Rejection, ...] = ()

    @property
    def style_description(self) -> str:
        return "; ".join(dict.fromkeys(f"{change.style}: {change.reason}" for change in self.plan.changes))

    def to_payload(self) -> dict:
        # No success/recheck claims: these are unvalidated candidate artifacts.
        return {
            "git_patch": self.git_patch,
            "style_description": self.style_description,
            "plan": self.plan.to_payload(),
            "rejected_candidates": [item.to_payload() for item in self.rejected_candidates],
        }

    def to_repair_payload(self, recheck: Mapping[str, str]) -> dict:
        """Build the REPAIR output after E10 has performed real checks."""
        values = {}
        for key in ("build", "test", "detection"):
            value = recheck.get(key) if isinstance(recheck, Mapping) else None
            if not isinstance(value, str) or not value:
                raise ValueError(f"recheck.{key} is required")
            values[key] = value
        return {
            "git_patch": self.git_patch,
            "style_description": self.style_description,
            "recheck": values,
            "rejected_candidates": [item.to_payload() for item in self.rejected_candidates],
        }
