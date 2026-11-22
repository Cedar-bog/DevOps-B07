"""E10 gate records: validation JSON, scenario artifacts and results.csv."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from .accept import write_atomic

CHECKS = ("patch_applies", "build", "test", "header_change", "no_missing")
SCENARIOS = ("accept", "reject-test", "reject-patch")


def _sensitive(path: Path) -> bool:
    return any(
        part.lower() == ".env" or part.lower().startswith(".env.")
        for part in path.resolve().parts
    )


def validation_payload(
    *,
    accepted: bool,
    checks: dict[str, bool],
    source_unchanged: bool,
    rollback: bool,
    error: str | None = None,
    details: dict | None = None,
) -> dict:
    payload = {
        "accepted": bool(accepted),
        "checks": {name: bool(checks.get(name, False)) for name in CHECKS},
        "source_unchanged": bool(source_unchanged),
        "rollback": bool(rollback),
        "error": error,
    }
    if details:
        payload["details"] = details
    return payload


def scenario_passed(scenario: str, validation: dict) -> bool:
    checks = validation.get("checks", {})
    if scenario == "accept":
        return (
            validation.get("accepted") is True
            and all(checks.get(name) is True for name in CHECKS)
            and validation.get("source_unchanged") is False
            and validation.get("rollback") is False
        )
    if scenario == "reject-test":
        return (
            validation.get("accepted") is False
            and checks.get("build") is True
            and checks.get("test") is False
            and validation.get("source_unchanged") is True
            and validation.get("rollback") is True
        )
    if scenario == "reject-patch":
        return (
            validation.get("accepted") is False
            and checks.get("patch_applies") is False
            and validation.get("source_unchanged") is True
            and validation.get("rollback") is True
            and bool(validation.get("error"))
        )
    return False


def gate_payload(*, passed: bool, rows: int, scenarios: list[dict]) -> dict:
    return {
        "schema_version": "1.0",
        "passed": bool(passed),
        "rows": rows,
        "scenarios": scenarios,
    }


def write_json(path: Path | str, payload: dict) -> Path:
    target = Path(path)
    if _sensitive(target):
        raise ValueError("output must not be a sensitive path")
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    return write_atomic(target, text.encode("utf-8"))


def write_text(path: Path | str, text: str) -> Path:
    target = Path(path)
    if _sensitive(target):
        raise ValueError("output must not be a sensitive path")
    return write_atomic(target, text.encode("utf-8"))


def write_results_csv(path: Path | str, rows: list[dict]) -> Path:
    fields = ["scenario", *CHECKS, "accepted", "source_unchanged", "rollback", "passed", "error"]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return write_text(path, buffer.getvalue())


def write_scenario_artifacts(
    output_dir: Path | str,
    scenario: str,
    *,
    validation: dict,
    build_log: str = "",
    incremental_log: str = "",
    recheck: dict | None = None,
) -> Path:
    target = Path(output_dir) / scenario
    target.mkdir(parents=True, exist_ok=True)
    write_json(target / "validation.json", validation)
    write_text(target / "build.log", build_log)
    write_text(target / "incremental.log", incremental_log)
    write_json(target / "recheck.json", recheck or {})
    return target
