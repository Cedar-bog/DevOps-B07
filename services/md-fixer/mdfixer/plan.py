"""E9 candidate handoff records; acceptance/recheck belong to E10."""

from __future__ import annotations

from dataclasses import asdict, dataclass


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

    @property
    def style_description(self) -> str:
        return "; ".join(dict.fromkeys(f"{change.style}: {change.reason}" for change in self.plan.changes))

    def to_payload(self) -> dict:
        # No success/recheck claims: these are unvalidated candidate artifacts.
        return {"git_patch": self.git_patch, "style_description": self.style_description, "plan": self.plan.to_payload()}
