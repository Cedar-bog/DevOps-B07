"""Read-only smoke checks for the MDFixer service.

The check confirms that the E3 MDFixer fixture and its fixed MD report are
present and internally consistent. It does not build a declaration graph,
classify declaration styles or generate patches; those belong to later E9
tasks.
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
STYLES = ("target", "macro", "hybrid", "implicit")
STYLE_FILES = ("Makefile.before", "reference.patch")
EXPECTED_MISSING = ("main.o", "config.h")
EXPECTED_REDUNDANT = ("main.o", "unused.h")

_COMMIT = re.compile(r"^[0-9a-f]{40}$")


@dataclass
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


def _root_for_fixture(fixture: Path) -> Path:
    if fixture.name == FIXTURE_NAME and fixture.parent.name == "fixtures":
        return fixture.parent.parent
    return find_repo_root()


def _check(checks: list[Check], name: str, ok: bool, detail: str) -> None:
    checks.append(Check(name=name, ok=ok, detail=detail))


def _is_commit(value: object) -> bool:
    return isinstance(value, str) and bool(_COMMIT.match(value))


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


def _resolve_position_file(fixture: Path, position_file: str) -> Path | None:
    position = Path(position_file)
    if position.is_absolute():
        return position if position.is_file() else None
    candidates = [fixture.parent.parent / position, _root_for_fixture(fixture) / position]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _check_report(
    checks: list[Check],
    fixture: Path,
    report_path: Path,
) -> tuple[list, str | None]:
    if not report_path.is_file():
        _check(checks, "report-json", False, f"report not found: {report_path}")
        return [], None

    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _check(checks, "report-json", False, f"cannot parse report: {exc}")
        return [], None

    if not isinstance(payload, dict):
        _check(checks, "report-json", False, "report root is not a JSON object")
        return [], None
    _check(checks, "report-json", True, "report parsed")

    schema_version = payload.get("schema_version")
    _check(
        checks,
        "report-schema-version",
        schema_version == SCHEMA_VERSION,
        f"schema_version={schema_version!r}",
    )

    findings = payload.get("findings")
    if not isinstance(findings, list):
        _check(checks, "report-findings", False, "findings is not a list")
        findings = []
    else:
        missing_ok = _finding_present(findings, "MISSING", *EXPECTED_MISSING)
        redundant_ok = _finding_present(findings, "REDUNDANT", *EXPECTED_REDUNDANT)
        _check(
            checks,
            "report-findings",
            missing_ok and redundant_ok,
            "expected MISSING {} -> {}: {}; expected REDUNDANT {} -> {}: {}".format(
                EXPECTED_MISSING[0],
                EXPECTED_MISSING[1],
                "present" if missing_ok else "absent",
                EXPECTED_REDUNDANT[0],
                EXPECTED_REDUNDANT[1],
                "present" if redundant_ok else "absent",
            ),
        )

    repository = payload.get("repository")
    top_commit = repository.get("commit") if isinstance(repository, dict) else None
    finding_commits = [
        finding.get("commit") for finding in findings if isinstance(finding, dict)
    ]
    commit_ok = (
        _is_commit(top_commit)
        and bool(finding_commits)
        and all(commit == top_commit for commit in finding_commits)
    )
    _check(
        checks,
        "report-commit",
        commit_ok,
        "repository commit={!r}, finding commits consistent={}".format(
            top_commit, commit_ok
        ),
    )

    expected = next(
        (
            finding
            for finding in findings
            if isinstance(finding, dict)
            and finding.get("type") == "MISSING"
            and finding.get("target") == EXPECTED_MISSING[0]
            and finding.get("dependency") == EXPECTED_MISSING[1]
        ),
        None,
    )
    if expected is None:
        _check(checks, "report-position", False, "expected MISSING finding not found")
        return findings, top_commit

    position = expected.get("position")
    if not isinstance(position, dict):
        _check(checks, "report-position", False, "MISSING finding has no position")
        return findings, top_commit

    position_file = position.get("file")
    line = position.get("line")
    if not isinstance(position_file, str) or not isinstance(line, int) or line < 1:
        _check(
            checks,
            "report-position",
            False,
            f"invalid position: file={position_file!r} line={line!r}",
        )
        return findings, top_commit

    target = _resolve_position_file(fixture, position_file)
    if target is None:
        _check(
            checks,
            "report-position",
            False,
            f"position file not found: {position_file}",
        )
        return findings, top_commit

    lines = target.read_text(encoding="utf-8").splitlines()
    if line > len(lines):
        _check(
            checks,
            "report-position",
            False,
            f"line {line} exceeds {target} with {len(lines)} lines",
        )
        return findings, top_commit

    declaration = position.get("declaration")
    actual_line = lines[line - 1].strip()
    if declaration is not None and actual_line != str(declaration).strip():
        _check(
            checks,
            "report-position",
            False,
            "line {} of {} is {!r}, report says {!r}".format(
                line, position_file, actual_line, declaration
            ),
        )
        return findings, top_commit

    _check(
        checks,
        "report-position",
        True,
        f"{position_file}:{line} -> {actual_line}",
    )
    return findings, top_commit


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

    for style in STYLES:
        style_dir = fixture / "styles" / style
        missing_style_files = [
            name for name in STYLE_FILES if not (style_dir / name).is_file()
        ]
        _check(
            checks,
            f"style:{style}",
            not missing_style_files,
            "present"
            if not missing_style_files
            else f"missing: {', '.join(missing_style_files)}",
        )

    _check_report(checks, fixture, report_path)
    result.finished_at = _now_iso()
    return result


def write_result(result: SmokeResult, path: Path | str) -> Path:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result.to_payload(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target
