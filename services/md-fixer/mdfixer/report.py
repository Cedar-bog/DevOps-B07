"""Validate the shared ERROR_REPORT consumer contract and locate MISSINGs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from .guard import RepairError, Source, project_file, relative_path, tracked_path
from .makefile import Makefile, Rule

_SHA = re.compile(r"[0-9a-f]{40}")
_DETECTORS = {"BUILDCHECKER", "ECHECKER", "INSTRUCTOR_ORACLE"}


@dataclass(frozen=True)
class Finding:
    finding_id: str
    target: str
    dependency: str
    line: int
    rule: Rule


@dataclass(frozen=True)
class Report:
    commit: str
    configuration_id: str
    missing: tuple[Finding, ...]
    ignored: tuple[str, ...]


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_report(
    payload: Mapping, source: Source, document: Makefile, *, configuration_id: str | None = None,
) -> Report:
    def invalid(message: str) -> None:
        raise RepairError("INVALID_REPORT", message)

    if not isinstance(payload, Mapping) or payload.get("schema_version") != "1.0":
        invalid("expected ERROR_REPORT schema_version 1.0")
    if set(payload) - {"schema_version", "repository", "configuration_id", "producer_job_id", "findings"}:
        invalid("unknown report fields")
    repository = payload.get("repository")
    if not isinstance(repository, Mapping) or set(repository) != {"url", "commit"} or not _nonempty(repository.get("url")):
        invalid("repository requires url and commit")
    try:
        url = urlsplit(repository["url"])
        if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password:
            invalid("repository.url must be an absolute http(s) URL without credentials")
    except ValueError as exc:
        raise RepairError("INVALID_REPORT", "invalid repository URL") from exc
    commit = repository.get("commit")
    if not isinstance(commit, str) or not _SHA.fullmatch(commit):
        invalid("repository.commit must be a full lowercase SHA")
    if commit != source.commit:
        raise RepairError("STALE_REPORT", "report commit does not match project HEAD")
    config = payload.get("configuration_id")
    if not _nonempty(config):
        invalid("configuration_id is required")
    if configuration_id is not None and config != configuration_id:
        raise RepairError("STALE_REPORT", "report configuration does not match the requested configuration")
    if "producer_job_id" in payload and not _nonempty(payload["producer_job_id"]):
        invalid("producer_job_id must be a nonempty string")
    items = payload.get("findings")
    if not isinstance(items, list):
        invalid("findings must be an array")
    missing, ignored = [], []
    ids: set[str] = set()
    edges: set[tuple[str, str]] = set()
    allowed = {"finding_id", "type", "target", "dependency", "commit", "position", "detector", "configuration_id", "evidence_uri"}
    for item in items:
        if not isinstance(item, Mapping) or set(item) - allowed:
            invalid("unknown or invalid finding fields")
        required = ("finding_id", "type", "target", "dependency", "commit", "detector")
        if any(not _nonempty(item.get(key)) for key in required):
            invalid("finding is missing required fields")
        identity = item["finding_id"]
        if identity in ids:
            invalid("duplicate finding_id")
        ids.add(identity)
        if item["commit"] != source.commit:
            raise RepairError("STALE_REPORT", "finding commit does not match project HEAD")
        if item["detector"] not in _DETECTORS or item["type"] not in {"MISSING", "REDUNDANT"}:
            invalid("unknown finding type or detector")
        if "configuration_id" in item and item["configuration_id"] != config:
            raise RepairError("STALE_REPORT", "finding configuration differs from the report")
        if "evidence_uri" in item and not _nonempty(item["evidence_uri"]):
            invalid("evidence_uri must be a nonempty string")
        position = item.get("position")
        if not isinstance(position, Mapping) or set(position) - {"file", "line", "declaration"}:
            invalid("finding requires a valid position")
        if not _nonempty(position.get("file")):
            invalid("position.file is required")
        line = position.get("line")
        if type(line) is not int or line < 1 or line > len(source.text.splitlines()):
            invalid("position.line must identify an existing declaration")
        relative_path(position.get("file"))
        if "declaration" in position and not isinstance(position["declaration"], str):
            invalid("position.declaration must be a string")
        if relative_path(position.get("file")) != source.name:
            raise RepairError("UNSUPPORTED_TARGET", "finding points to a different Makefile")
        if "declaration" in position and position["declaration"] != source.text.splitlines()[line - 1].strip():
            raise RepairError("STALE_REPORT", "reported declaration no longer matches the Makefile")
        target = relative_path(item["target"])
        dependency = relative_path(item["dependency"])
        if item["type"] == "REDUNDANT":
            ignored.append(identity)
            continue  # REDUNDANT findings are not consumed, but their report identity is checked.
        dependency_file = project_file(source.path.parent, dependency)
        if not dependency_file.is_relative_to(source.root):
            raise RepairError("UNSAFE_PATH", "dependency is outside the project")
        if not tracked_path(source.root, dependency_file):
            raise RepairError("STALE_REPORT", "dependency is not tracked at the reported commit")
        rule = document.rule_at(line, target)
        if rule.implicit:
            stem = rule.stem(target)
            for prerequisite in rule.prerequisites:
                prerequisite_file = project_file(source.path.parent, prerequisite.replace("%", stem))
                if not tracked_path(source.root, prerequisite_file):
                    raise RepairError("STALE_REPORT", "implicit prerequisite is not tracked at the reported commit")
        pair = (target, dependency)
        if pair in edges:
            continue  # Distinct detectors may report the same missing edge.
        edges.add(pair)
        missing.append(Finding(identity, target, dependency, line, rule))
    return Report(commit, config, tuple(missing), tuple(ignored))


def load_report(path: Path | str) -> dict:
    # Explicit report paths are input artifacts, never automatically discovered.
    path = Path(path)
    if any(part.lower() == ".env" or part.lower().startswith(".env.") for part in path.resolve().parts):
        raise RepairError("UNSAFE_PATH", "not a report path")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RepairError("INVALID_REPORT", "cannot read JSON report") from exc
    if not isinstance(payload, dict):
        raise RepairError("INVALID_REPORT", "report must be an object")
    return payload
