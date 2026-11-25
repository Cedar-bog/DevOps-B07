"""Contract-driven REPAIR runner for the MDFixer HTTP service.

The existing ``mdfixer validate`` command remains the local fixture test tool.
This module implements the REPAIR contract: clone a repository, resolve an
artifact report, run the caller-provided build and verify commands, then run
incremental rebuild and declaration recheck before returning a patch.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .build import safe_env
from .guard import RepairError, read_source
from draft.llm import LLMClient
from .makefile import parse_makefile
from .patch import generate_patch
from .recheck import check_declarations, run_incremental
from .report import validate_report
from .sandbox import apply_candidate, create_sandbox, remove_sandbox

MAX_REPO_BYTES = 500 * 1024 * 1024
MAX_REPO_FILES = 100_000
MAX_REPORT_BYTES = 16 * 1024 * 1024
CLONE_TIMEOUT = 300.0
COMMAND_TIMEOUT = 900.0
TASK_TIMEOUT = 1800.0
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_TRACE = re.compile(r"^trace-[a-z0-9-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_INPUT_KEYS = {
    "repository",
    "commit",
    "md_report_uri",
    "md_report_sha256",
    "makefile",
    "env",
    "recheck",
    "idempotency_key",
}
_OPTIONAL_INPUT_KEYS = {"time_limit"}
_ENV_KEYS = {"build", "verify"}
_OPTIONAL_ENV_KEYS = {"verify_expected_output"}
_RECHECK_KEYS = {
    "header",
    "macro",
    "old_value",
    "new_value",
    "target",
    "program",
    "expected_output",
    "timeout_seconds",
}
_REPAIR_CODE_BY_REASON = {
    "INVALID_REPORT": "REPAIR_6001",
    "STALE_REPORT": "REPAIR_6002",
    "UNSUPPORTED_TARGET": "REPAIR_6005",
    "UNSAFE_PATH": "REPAIR_6005",
    "MISSING_INPUT": "REPAIR_6005",
}


class ValidationError(ValueError):
    """The request does not satisfy the shared REPAIR contract."""


class ServiceError(RuntimeError):
    """The accepted job could not be completed."""

    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True)
class CommandOutcome:
    ok: bool
    returncode: int | None
    command: str
    stdout: str
    stderr: str
    timed_out: bool = False
    error: str | None = None

    @property
    def log(self) -> str:
        text = f"$ {self.command}\n{self.stdout}"
        if self.stderr:
            text += "\n" + self.stderr
        if self.error:
            text += "\nerror: " + self.error
        return text.strip()


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _artifact_path(uri: object) -> tuple[str, str, str]:
    if not isinstance(uri, str) or not uri.startswith("artifact://"):
        raise ValidationError("md_report_uri must start with artifact://")
    parts = uri[len("artifact://"):].split("/")
    if len(parts) != 3 or any(not part or part in {".", ".."} or "\\" in part for part in parts):
        raise ValidationError("md_report_uri must be artifact://<pair>/<job>/<file>")
    return parts[0], parts[1], parts[2]


def validate_request(payload: object) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValidationError("request must be a JSON object")
    if set(payload) - {"schema_version", "trace_id", "job_type", "input"}:
        raise ValidationError("unknown request fields")
    if payload.get("schema_version") != "1.0":
        raise ValidationError("schema_version must be 1.0")
    if payload.get("job_type") != "REPAIR":
        raise ValidationError("job_type must be REPAIR")
    trace_id = payload.get("trace_id")
    if not isinstance(trace_id, str) or not _TRACE.fullmatch(trace_id):
        raise ValidationError("trace_id must match ^trace-[a-z0-9-]+$")

    data = payload.get("input")
    if not isinstance(data, Mapping):
        raise ValidationError("input must be an object")
    if set(data) - _INPUT_KEYS - _OPTIONAL_INPUT_KEYS:
        raise ValidationError("input contains unknown fields")
    missing = _INPUT_KEYS - set(data)
    if missing:
        raise ValidationError("input is missing fields: " + ", ".join(sorted(missing)))

    repository = data.get("repository")
    if not isinstance(repository, Mapping) or set(repository) != {"url", "commit"}:
        raise ValidationError("repository requires url and commit")
    url = repository.get("url")
    if not _nonempty(url):
        raise ValidationError("repository.url is required")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValidationError("repository.url must be an absolute http(s) URL without credentials")
    repo_commit = repository.get("commit")
    if not isinstance(repo_commit, str) or not _COMMIT.fullmatch(repo_commit):
        raise ValidationError("repository.commit must be a full lowercase SHA")
    commit = data.get("commit")
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise ValidationError("commit must be a full lowercase SHA")
    if commit != repo_commit:
        raise ValidationError("commit and repository.commit must match")

    _artifact_path(data.get("md_report_uri"))
    if not _nonempty(data.get("makefile")):
        raise ValidationError("makefile is required")

    report_sha256 = data.get("md_report_sha256")
    if not isinstance(report_sha256, str) or not _SHA256.fullmatch(report_sha256):
        raise ValidationError("md_report_sha256 must be a lowercase SHA256")

    env = data.get("env")
    if not isinstance(env, Mapping):
        raise ValidationError("env must be an object")
    if set(env) - _ENV_KEYS - _OPTIONAL_ENV_KEYS or _ENV_KEYS - set(env):
        raise ValidationError("env requires build and verify")
    if not _nonempty(env.get("build")) or not _nonempty(env.get("verify")):
        raise ValidationError("env.build and env.verify are required")
    expected_output = env.get("verify_expected_output")
    if expected_output is not None and not isinstance(expected_output, str):
        raise ValidationError("env.verify_expected_output must be a string")

    recheck = data.get("recheck")
    if not isinstance(recheck, Mapping):
        raise ValidationError("recheck must be an object")
    if set(recheck) - _RECHECK_KEYS:
        raise ValidationError("recheck contains unknown fields")
    required_recheck = _RECHECK_KEYS - {"timeout_seconds"}
    missing_recheck = required_recheck - set(recheck)
    if missing_recheck:
        raise ValidationError(
            "recheck is missing fields: " + ", ".join(sorted(missing_recheck))
        )
    for name in ("header", "macro", "target", "program"):
        if not _nonempty(recheck.get(name)):
            raise ValidationError(f"recheck.{name} is required")
    for name in ("old_value", "new_value", "expected_output"):
        if not isinstance(recheck.get(name), str):
            raise ValidationError(f"recheck.{name} must be a string")
    timeout_seconds = recheck.get("timeout_seconds")
    if timeout_seconds is not None and (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds < 1
    ):
        raise ValidationError("recheck.timeout_seconds must be a positive integer")

    time_limit = data.get("time_limit")
    if time_limit is not None and (
        not isinstance(time_limit, int) or isinstance(time_limit, bool) or time_limit < 1
    ):
        raise ValidationError("time_limit must be a positive integer")

    if not _nonempty(data.get("idempotency_key")):
        raise ValidationError("idempotency_key is required")

    result = {
        "repository": {"url": url, "commit": repo_commit},
        "commit": commit,
        "md_report_uri": data["md_report_uri"],
        "makefile": data["makefile"],
        "env": {
            "build": env["build"],
            "verify": env["verify"],
        },
        "idempotency_key": data["idempotency_key"],
    }
    if expected_output is not None:
        result["env"]["verify_expected_output"] = expected_output
    result["md_report_sha256"] = report_sha256
    result["recheck"] = dict(recheck)
    if time_limit is not None:
        result["time_limit"] = time_limit
    return result


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise ServiceError("EXEC_4002", "task time limit exceeded")
    return left


def _clone(repository: Mapping[str, str], destination: Path, deadline: float) -> None:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        clone = subprocess.run(
            ["git", "clone", "--no-checkout", repository["url"], str(destination)],
            capture_output=True,
            text=True,
            timeout=min(CLONE_TIMEOUT, _remaining(deadline)),
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ServiceError("EXEC_4002", "git clone timed out") from exc
    except OSError as exc:
        raise ServiceError("REPAIR_6000", f"cannot start git clone: {exc}") from exc
    if clone.returncode:
        raise ServiceError("REPAIR_6000", (clone.stderr or clone.stdout).strip() or "git clone failed")

    try:
        checkout = subprocess.run(
            ["git", "-C", str(destination), "checkout", "--detach", repository["commit"]],
            capture_output=True,
            text=True,
            timeout=min(60.0, _remaining(deadline)),
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ServiceError("EXEC_4002", "git checkout timed out") from exc
    if checkout.returncode:
        raise ServiceError(
            "REPAIR_6000",
            (checkout.stderr or checkout.stdout).strip() or "git checkout failed",
        )

    total = 0
    files = 0
    for path in destination.rglob("*"):
        if not path.is_file():
            continue
        files += 1
        try:
            total += path.stat().st_size
        except OSError:
            continue
    if total > MAX_REPO_BYTES or files > MAX_REPO_FILES:
        shutil.rmtree(destination, ignore_errors=True)
        raise ServiceError(
            "REPAIR_6000",
            f"repository exceeds limits: {total} bytes, {files} files",
        )


def _repair_code(reason: str) -> str:
    return _REPAIR_CODE_BY_REASON.get(reason, "REPAIR_6000")


def _parse_report(raw: bytes) -> dict:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ServiceError("REPAIR_6001", "cannot read JSON report") from exc
    if not isinstance(payload, dict):
        raise ServiceError("REPAIR_6001", "report must be an object")
    return payload


def _fetch_report(url: str) -> tuple[bytes, str | None, str | None]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(MAX_REPORT_BYTES + 1)
            if len(raw) > MAX_REPORT_BYTES:
                raise ServiceError("REPAIR_6001", "report artifact is too large")
            digest = hashlib.sha256(raw).hexdigest()
            return (
                raw,
                response.headers.get("X-Artifact-Sha256"),
                response.headers.get("Content-Type"),
            )
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ServiceError("REPAIR_6001", f"report artifact not found: {url}") from exc
        if exc.code == 403:
            raise ServiceError("REPAIR_6005", f"report artifact access denied: {url}") from exc
        raise ServiceError("REPAIR_6000", f"report artifact request failed: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ServiceError("REPAIR_6000", f"report artifact request failed: {exc}") from exc


def _resolve_report(
    data_dir: Path,
    uri: str,
    expected_sha256: str | None,
    artifact_base_url: str | None,
) -> dict:
    pair, job, name = _artifact_path(uri)
    path = data_dir / "artifacts" / pair / job / name
    if path.is_file():
        if path.suffix.lower() != ".json":
            raise ServiceError("REPAIR_6003", "report artifact is not JSON")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ServiceError("REPAIR_6001", f"cannot read report artifact: {exc}") from exc
        digest = hashlib.sha256(raw).hexdigest()
        header_sha = None
        content_type = None
    elif artifact_base_url:
        base = artifact_base_url.rstrip("/")
        url = "/".join(
            (base, urllib.parse.quote(pair), urllib.parse.quote(job), urllib.parse.quote(name))
        )
        raw, header_sha, content_type = _fetch_report(url)
        digest = hashlib.sha256(raw).hexdigest()
        if content_type and "json" not in content_type.lower():
            raise ServiceError("REPAIR_6003", f"report artifact is not JSON: {content_type}")
    else:
        raise ServiceError("REPAIR_6001", f"report artifact not found: {uri}")

    if header_sha and header_sha.lower() != digest:
        raise ServiceError("REPAIR_6004", "report artifact SHA256 header mismatch")
    if expected_sha256 and expected_sha256.lower() != digest:
        raise ServiceError("REPAIR_6004", "report artifact SHA256 mismatch")
    return _parse_report(raw)


def _makefile_path(data: Mapping[str, Any]) -> str:
    return data["makefile"]


def _redundant_edges(report: Mapping) -> tuple[tuple[str, str], ...]:
    findings = report.get("findings")
    if not isinstance(findings, list):
        return ()
    return tuple(
        (item["target"], item["dependency"])
        for item in findings
        if isinstance(item, Mapping)
        and item.get("type") == "REDUNDANT"
        and isinstance(item.get("target"), str)
        and isinstance(item.get("dependency"), str)
    )


def _run_command(command: str, cwd: Path, timeout: float) -> CommandOutcome:
    try:
        result = subprocess.run(
            ["/bin/sh", "-lc", command],
            cwd=str(cwd),
            env=safe_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandOutcome(False, None, command, "", "", True, f"timed out after {timeout:g}s")
    except OSError as exc:
        return CommandOutcome(False, None, command, "", "", False, str(exc))
    return CommandOutcome(
        result.returncode == 0,
        result.returncode,
        command,
        result.stdout or "",
        result.stderr or "",
    )


def _rejected(patch: str, reason: str) -> dict[str, str]:
    return {"patch": patch, "reason": reason}


def _validation(
    *,
    patch_applies: bool = False,
    build: bool = False,
    test: bool = False,
    header_change: bool | None = None,
    no_missing: bool = False,
    redundant_preserved: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "patch_applies": patch_applies,
        "build": build,
        "test": test,
        "no_missing": no_missing,
        "redundant_preserved": redundant_preserved,
    }
    if header_change is not None:
        result["header_change"] = header_change
    return result


def _build_payload(ok: bool, command: str, log: str) -> dict[str, Any]:
    return {"ok": ok, "command": command, "log": log}


def _test_payload(
    ok: bool,
    command: str,
    log: str,
    observed: str,
    expected: str | None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "command": command,
        "log": log,
        "observed_output": observed,
        "expected_output": expected,
    }


def _detection_payload(
    ok: bool,
    message: str,
    missing: tuple[tuple[str, str], ...] = (),
    redundant: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    return {
        "ok": ok,
        "message": message,
        "missing_remaining": [list(edge) for edge in missing],
        "redundant_missing": [list(edge) for edge in redundant],
    }


def _incremental_not_run(expected: str) -> dict[str, Any]:
    return {
        "ok": False,
        "header_change": False,
        "rebuilt": False,
        "build_ok": False,
        "program_ok": False,
        "observed_output": "",
        "expected_output": expected,
        "error": "NOT_RUN",
    }


def _validation(
    *,
    patch_applies: bool = False,
    build: bool = False,
    test: bool = False,
    header_change: bool = False,
    no_missing: bool = False,
    redundant_preserved: bool = False,
) -> dict[str, Any]:
    return {
        "patch_applies": patch_applies,
        "build": build,
        "test": test,
        "header_change": header_change,
        "no_missing": no_missing,
        "redundant_preserved": redundant_preserved,
    }


def _output(
    patch: str,
    style: str,
    *,
    build: Mapping[str, Any],
    test: Mapping[str, Any],
    detection: Mapping[str, Any],
    incremental: Mapping[str, Any],
    rejected: list[dict[str, str]],
    validation: Mapping[str, Any],
    accepted: bool = False,
) -> dict[str, Any]:
    return {
        "git_patch": patch,
        "style_description": style,
        "recheck": {
            "build": dict(build),
            "test": dict(test),
            "detection": dict(detection),
            "incremental": dict(incremental),
        },
        "accepted": accepted,
        "validation": dict(validation),
        "rejected_candidates": rejected,
    }


def run_contract_repair(
    data: Mapping[str, Any],
    llm: LLMClient | None,
    job_id: str,
    work_dir: Path,
    data_dir: Path,
    *,
    artifact_base_url: str | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + float(data.get("time_limit", TASK_TIMEOUT))
    work_dir.mkdir(parents=True, exist_ok=True)
    repo = work_dir / "repo"
    _clone(data["repository"], repo, deadline)

    report = _resolve_report(
        data_dir,
        data["md_report_uri"],
        data["md_report_sha256"],
        artifact_base_url,
    )
    makefile = _makefile_path(data)
    try:
        source = read_source(repo, makefile, commit=data["commit"])
        document = parse_makefile(source.text)
        validated = validate_report(report, source, document)
    except RepairError as exc:
        raise ServiceError(_repair_code(exc.code), f"{exc.code}: {exc}") from exc

    try:
        generated = generate_patch(
            repo,
            report,
            makefile=makefile,
            commit=data["commit"],
            llm=llm,
            collect_rejections=True,
        )
    except RepairError as exc:
        raise ServiceError(_repair_code(exc.code), f"{exc.code}: {exc}") from exc

    patch = generated.git_patch
    style = generated.style_description
    recheck_data = data["recheck"]
    expected_incremental = recheck_data["expected_output"]
    expected_test = data["env"].get("verify_expected_output")
    build_command = data["env"]["build"]
    test_command = data["env"]["verify"]
    rejected = [item.to_payload() for item in generated.rejected_candidates]
    if not patch:
        detection = (
            "SUCCEEDED: no MISSING changes required"
            if not rejected
            else "REJECTED: no acceptable candidate"
        )
        return _output(
            "",
            style,
            build=_build_payload(False, build_command, "NOT_RUN: no accepted patch"),
            test=_test_payload(False, test_command, "NOT_RUN: no accepted patch", "", expected_test),
            detection=_detection_payload(False, detection),
            incremental=_incremental_not_run(expected_incremental),
            rejected=rejected,
            validation=_validation(),
        )

    sandbox = create_sandbox(repo, work_dir / "sandbox")
    try:
        applied = apply_candidate(sandbox, patch)
        if not applied.ok:
            reason = applied.error or "candidate patch was rejected"
            rejected.append(_rejected(patch, reason))
            return _output(
                "",
                style,
                build=_build_payload(False, build_command, "NOT_RUN: patch rejected"),
                test=_test_payload(False, test_command, "NOT_RUN: patch rejected", "", expected_test),
                detection=_detection_payload(False, f"REJECTED: {reason}"),
                incremental=_incremental_not_run(expected_incremental),
                rejected=rejected,
                validation=_validation(),
            )

        build = _run_command(build_command, sandbox, min(COMMAND_TIMEOUT, _remaining(deadline)))
        if not build.ok:
            reason = build.error or (build.stderr or build.stdout).strip() or "build command failed"
            rejected.append(_rejected(patch, reason))
            return _output(
                "",
                style,
                build=_build_payload(False, build_command, build.log),
                test=_test_payload(False, test_command, "NOT_RUN: build failed", "", expected_test),
                detection=_detection_payload(False, "NOT_RUN: build failed"),
                incremental=_incremental_not_run(expected_incremental),
                rejected=rejected,
                validation=_validation(patch_applies=True),
            )

        verify = _run_command(test_command, sandbox, min(COMMAND_TIMEOUT, _remaining(deadline)))
        observed_output = verify.stdout.rstrip("\r\n")
        output_matches = expected_test is None or observed_output == expected_test
        if not verify.ok or not output_matches:
            reason = verify.error or (verify.stderr or verify.stdout).strip() or "verify command failed"
            if verify.ok and not output_matches:
                reason = f"verify output mismatch: expected {expected_test!r}, got {observed_output!r}"
            rejected.append(_rejected(patch, reason))
            return _output(
                "",
                style,
                build=_build_payload(True, build_command, build.log),
                test=_test_payload(False, test_command, verify.log, observed_output, expected_test),
                detection=_detection_payload(False, "NOT_RUN: verify failed"),
                incremental=_incremental_not_run(expected_incremental),
                rejected=rejected,
                validation=_validation(patch_applies=True, build=True),
            )

        timeout = int(recheck_data.get("timeout_seconds", COMMAND_TIMEOUT))
        incremental_result = run_incremental(
            sandbox,
            header=recheck_data["header"],
            macro=recheck_data["macro"],
            old_value=recheck_data["old_value"],
            new_value=recheck_data["new_value"],
            target=recheck_data["target"],
            program=recheck_data["program"],
            timeout=min(timeout, int(_remaining(deadline))),
            env=safe_env(),
        )
        incremental = incremental_result.to_payload()

        try:
            patched_text = (sandbox / makefile).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ServiceError("REPAIR_6000", f"cannot read patched Makefile: {exc}") from exc
        declaration = check_declarations(
            patched_text,
            validated.missing,
            _redundant_edges(report),
            sandbox=sandbox,
        )
        validation = _validation(
            patch_applies=True,
            build=True,
            test=True,
            header_change=bool(incremental_result.header_change and incremental_result.rebuilt),
            no_missing=declaration.no_missing,
            redundant_preserved=declaration.redundant_preserved,
        )
        detection = _detection_payload(
            declaration.ok,
            "no MISSING findings remain; REDUNDANT findings preserved"
            if declaration.ok
            else (declaration.error or "declaration recheck failed"),
            declaration.missing_remaining,
            declaration.redundant_missing,
        )
        if not declaration.ok:
            reason = declaration.error or "declaration recheck failed"
            if declaration.missing_remaining:
                reason = "MISSING edges remain: " + ", ".join(
                    f"{target} -> {dependency}" for target, dependency in declaration.missing_remaining
                )
            elif declaration.redundant_missing:
                reason = "REDUNDANT edges were removed"
            rejected.append(_rejected(patch, reason))
            return _output(
                "",
                style,
                build=_build_payload(True, build_command, build.log),
                test=_test_payload(True, test_command, verify.log, observed_output, expected_test),
                detection=detection,
                incremental=incremental,
                rejected=rejected,
                validation=validation,
            )

        if not incremental_result.ok:
            reason = incremental.get("error") or "incremental rebuild failed"
            rejected.append(_rejected(patch, reason))
            return _output(
                "",
                style,
                build=_build_payload(True, build_command, build.log),
                test=_test_payload(True, test_command, verify.log, observed_output, expected_test),
                detection=detection,
                incremental=incremental,
                rejected=rejected,
                validation=validation,
            )

        return _output(
            patch,
            style,
            build=_build_payload(True, build_command, build.log),
            test=_test_payload(True, test_command, verify.log, observed_output, expected_test),
            detection=detection,
            incremental=incremental,
            rejected=rejected,
            validation=validation,
            accepted=True,
        )
    finally:
        remove_sandbox(sandbox)
