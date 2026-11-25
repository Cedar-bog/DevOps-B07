"""Candidate handoff records; acceptance and recheck are owned by the validation stage."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Rejection:
    """A candidate-level rejection record.

    Internal reasons stay on the candidate record; the service maps them to
    B07-owned REPAIR_* job error codes when the task cannot continue.
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
