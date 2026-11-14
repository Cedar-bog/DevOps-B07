"""Minimal read-only smoke checks for the MDFixer fixture.

The smoke check confirms that the fixture files and the fixed MD report are
present and structurally usable. It deliberately does not follow paths from the
report, build a declaration graph, generate patches, or read project files.
Those checks belong to the E9 pipeline.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SERVICE = "mdfixer"
SCHEMA_VERSION = "1.0"
FIXTURE_NAME = "md-fixer"
REPORT_NAME = "fixed-md-report.json"
REQUIRED_FILES = ("Makefile", "main.c", "config.h", "unused.h", REPORT_NAME)
REQUIRED_STYLES = {
    "target": ("Makefile.before", "reference.patch"),
    "macro": ("Makefile.before", "reference.patch"),
    "hybrid": ("Makefile.before", "reference.patch"),
    "implicit": ("Makefile.before", "reference.patch"),
}
EXPECTED_MISSING = ("main.o", "config.h")
EXPECTED_REDUNDANT = ("main.o", "unused.h")

_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_DETECTORS = {"BUILDCHECKER", "ECHECKER", "INSTRUCTOR_ORACLE"}


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class SmokeResult:
    fixture_dir: str
    report: str
    checks: list[Check] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(check.ok for check in self.checks)

    def to_payload(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "service": SERVICE,
            "ok": self.ok,
            "fixture_dir": self.fixture_dir,
            "report": self.report,
            "checks": [
                {"name": check.name, "ok": check.ok, "detail": check.detail}
                for check in self.checks
            ],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_repo_root(start: Path | None = None) -> Path:
    base = (start or Path(__file__)).resolve()
    for parent in (base, *base.parents):
        if (parent / "fixtures" / FIXTURE_NAME).is_dir():
            return parent
    return Path.cwd()


def _check(checks: list[Check], name: str, ok: bool, detail: str) -> None:
    checks.append(Check(name=name, ok=ok, detail=detail))


def _sensitive_path(path: Path) -> bool:
    return any(
        part.lower() == ".env" or part.lower().startswith(".env.")
        for part in path.resolve().parts
    )


def _finding_present(findings: list, kind: str, target: str, dependency: str) -> bool:
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if (
            finding.get("type") == kind
            and finding.get("target") == target
            and finding.get("dependency") == dependency
        ):
            return True
    return False


def _check_report(checks: list[Check], report_path: Path) -> None:
    if _sensitive_path(report_path):
        _check(checks, "report-json", False, "refusing to read a sensitive path")
        return
    if not report_path.is_file():
        _check(checks, "report-json", False, f"report not found: {report_path}")
        return
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _check(checks, "report-json", False, f"cannot parse report: {exc}")
        return
    if not isinstance(payload, dict):
        _check(checks, "report-json", False, "report root is not a JSON object")
        return
    _check(checks, "report-json", True, "report parsed")

    _check(
        checks,
        "report-schema-version",
        payload.get("schema_version") == SCHEMA_VERSION,
        f"schema_version={payload.get('schema_version')!r}",
    )

    repository = payload.get("repository")
    top_commit = repository.get("commit") if isinstance(repository, dict) else None
    _check(
        checks,
        "report-commit",
        isinstance(top_commit, str) and bool(_COMMIT.fullmatch(top_commit)),
        f"repository commit={top_commit!r}",
    )

    findings = payload.get("findings")
    if not isinstance(findings, list):
        _check(checks, "report-findings", False, "findings is not a list")
        return
    missing_ok = _finding_present(findings, "MISSING", *EXPECTED_MISSING)
    redundant_ok = _finding_present(findings, "REDUNDANT", *EXPECTED_REDUNDANT)
    _check(
        checks,
        "report-findings",
        missing_ok and redundant_ok,
        "MISSING main.o -> config.h: {}; REDUNDANT main.o -> unused.h: {}".format(
            "present" if missing_ok else "absent",
            "present" if redundant_ok else "absent",
        ),
    )

    valid_fields = True
    for finding in findings:
        if not isinstance(finding, dict):
            valid_fields = False
            break
        if finding.get("type") not in {"MISSING", "REDUNDANT"}:
            valid_fields = False
        if finding.get("detector") not in _DETECTORS:
            valid_fields = False
        commit = finding.get("commit")
        if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
            valid_fields = False
        if isinstance(top_commit, str) and commit != top_commit:
            valid_fields = False
    _check(
        checks,
        "report-fields",
        valid_fields,
        "finding types, detectors and commits are consistent"
        if valid_fields
        else "finding types, detectors or commits are invalid",
    )


def run_smoke(
    fixture_dir: Path | str,
    *,
    report: Path | str | None = None,
) -> SmokeResult:
    fixture = Path(fixture_dir).expanduser()
    report_path = Path(report).expanduser() if report else fixture / REPORT_NAME
    result = SmokeResult(
        fixture_dir=str(fixture.resolve()),
        report=str(report_path.resolve()),
        started_at=_now_iso(),
    )
    checks = result.checks

    if not fixture.is_dir():
        _check(checks, "fixture-dir", False, f"fixture directory not found: {fixture}")
        result.finished_at = _now_iso()
        return result
    _check(checks, "fixture-dir", True, str(fixture.resolve()))

    missing_files = [name for name in REQUIRED_FILES if not (fixture / name).is_file()]
    _check(
        checks,
        "fixture-files",
        not missing_files,
        "present" if not missing_files else f"missing: {', '.join(missing_files)}",
    )

    for style, names in REQUIRED_STYLES.items():
        style_dir = fixture / "styles" / style
        missing_style_files = [name for name in names if not (style_dir / name).is_file()]
        _check(
            checks,
            f"style:{style}",
            not missing_style_files,
            "present" if not missing_style_files else f"missing: {', '.join(missing_style_files)}",
        )

    _check_report(checks, report_path)
    result.finished_at = _now_iso()
    return result


def write_result(result: SmokeResult, path: Path | str) -> Path:
    target = Path(path).expanduser()
    if _sensitive_path(target):
        raise ValueError("refusing to write a sensitive path")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result.to_payload(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target
