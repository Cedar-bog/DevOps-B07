"""Contract-driven REPAIR runner for the MDFixer HTTP service.

The existing ``mdfixer validate`` command remains the local fixture test tool.
This module implements the strict REPAIR contract: clone a repository, resolve
an artifact report, run the caller-provided build and verify commands, and
return a patch plus declarative recheck results.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .build import safe_env
from .guard import RepairError, read_source
from draft.llm import LLMClient
from .makefile import parse_makefile
from .patch import generate_patch
from .recheck import check_declarations
from .report import load_report, validate_report
from .sandbox import apply_candidate, create_sandbox, remove_sandbox

MAX_REPO_BYTES = 500 * 1024 * 1024
MAX_REPO_FILES = 100_000
CLONE_TIMEOUT = 300.0
COMMAND_TIMEOUT = 900.0
TASK_TIMEOUT = 1800.0
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_TRACE = re.compile(r"^trace-[a-z0-9-]+$")
_INPUT_KEYS = {
    "repository",
    "commit",
    "md_report_uri",
    "makefile",
    "env",
    "verify",
    "idempotency_key",
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
    if not isinstance(data, Mapping) or set(data) != _INPUT_KEYS:
        raise ValidationError("input must contain exactly the REPAIR fields")

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

    env = data.get("env")
    if not isinstance(env, Mapping) or set(env) != {"build", "verify"}:
        raise ValidationError("env requires build and verify")
    if not _nonempty(env.get("build")) or not _nonempty(env.get("verify")):
        raise ValidationError("env.build and env.verify are required")
    if not _nonempty(data.get("verify")):
        raise ValidationError("verify is required")
    if data["verify"] != env["verify"]:
        raise ValidationError("verify must match env.verify")
    if not _nonempty(data.get("idempotency_key")):
        raise ValidationError("idempotency_key is required")

    return {
        "repository": {"url": url, "commit": repo_commit},
        "commit": commit,
        "md_report_uri": data["md_report_uri"],
        "makefile": data["makefile"],
        "env": {"build": env["build"], "verify": env["verify"]},
        "verify": data["verify"],
        "idempotency_key": data["idempotency_key"],
    }


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
        raise ServiceError("ANALYSIS_5001", f"cannot start git clone: {exc}") from exc
    if clone.returncode:
        raise ServiceError("ANALYSIS_5001", (clone.stderr or clone.stdout).strip() or "git clone failed")

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
            "ANALYSIS_5001",
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
            "ANALYSIS_5001",
            f"repository exceeds limits: {total} bytes, {files} files",
        )


def _resolve_report(data_dir: Path, uri: str) -> dict:
    pair, job, name = _artifact_path(uri)
    path = data_dir / "artifacts" / pair / job / name
    if not path.is_file():
        raise ServiceError("ANALYSIS_5001", f"report artifact not found: {uri}")
    try:
        return load_report(path)
    except RepairError as exc:
        raise ServiceError("ANALYSIS_5001", f"{exc.code}: {exc}") from exc


def _makefile_path(repo: Path, data: Mapping[str, Any], report: Mapping) -> str:
    value = data["makefile"]
    if "\n" not in value:
        return value
    target = "Makefile"
    for finding in report.get("findings", []):
        if isinstance(finding, Mapping) and finding.get("type") == "MISSING":
            position = finding.get("position")
            if isinstance(position, Mapping) and _nonempty(position.get("file")):
                target = position["file"]
                break
    path = repo / target
    if not path.is_file():
        raise ServiceError("ANALYSIS_5001", f"inline Makefile target not found: {target}")
    committed = path.read_text(encoding="utf-8", errors="replace")
    if committed.replace("\r\n", "\n").strip() != value.replace("\r\n", "\n").strip():
        raise ServiceError("ANALYSIS_5001", "inline Makefile does not match the committed file")
    return target


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


def _output(
    patch: str,
    style: str,
    *,
    build: str,
    test: str,
    detection: str,
    rejected: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "git_patch": patch,
        "style_description": style,
        "recheck": {"build": build, "test": test, "detection": detection},
        "rejected_candidates": rejected,
    }


def run_contract_repair(
    data: Mapping[str, Any],
    llm: LLMClient | None,
    job_id: str,
    work_dir: Path,
    data_dir: Path,
) -> dict[str, Any]:
    deadline = time.monotonic() + TASK_TIMEOUT
    work_dir.mkdir(parents=True, exist_ok=True)
    repo = work_dir / "repo"
    _clone(data["repository"], repo, deadline)

    report = _resolve_report(data_dir, data["md_report_uri"])
    makefile = _makefile_path(repo, data, report)
    try:
        source = read_source(repo, makefile, commit=data["commit"])
        document = parse_makefile(source.text)
        validated = validate_report(report, source, document)
    except RepairError as exc:
        raise ServiceError("ANALYSIS_5001", f"{exc.code}: {exc}") from exc

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
        raise ServiceError("ANALYSIS_5001", f"{exc.code}: {exc}") from exc

    patch = generated.git_patch
    style = generated.style_description
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
            build="NOT_RUN: no accepted patch",
            test="NOT_RUN: no accepted patch",
            detection=detection,
            rejected=rejected,
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
                build="NOT_RUN: patch rejected",
                test="NOT_RUN: patch rejected",
                detection=f"REJECTED: {reason}",
                rejected=rejected,
            )

        build = _run_command(data["env"]["build"], sandbox, min(COMMAND_TIMEOUT, _remaining(deadline)))
        if not build.ok:
            reason = build.error or (build.stderr or build.stdout).strip() or "build command failed"
            rejected.append(_rejected(patch, reason))
            return _output(
                "",
                style,
                build=f"REJECTED: {reason}",
                test="NOT_RUN: build failed",
                detection="NOT_RUN: build failed",
                rejected=rejected,
            )

        verify = _run_command(data["env"]["verify"], sandbox, min(COMMAND_TIMEOUT, _remaining(deadline)))
        if not verify.ok:
            reason = verify.error or (verify.stderr or verify.stdout).strip() or "verify command failed"
            rejected.append(_rejected(patch, reason))
            return _output(
                "",
                style,
                build=f"SUCCEEDED: {data['env']['build']}",
                test=f"REJECTED: {reason}",
                detection="NOT_RUN: verify failed",
                rejected=rejected,
            )

        try:
            patched_text = (sandbox / makefile).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ServiceError("ANALYSIS_5001", f"cannot read patched Makefile: {exc}") from exc
        declaration = check_declarations(
            patched_text,
            validated.missing,
            _redundant_edges(report),
            sandbox=sandbox,
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
                build=f"SUCCEEDED: {data['env']['build']}",
                test=f"SUCCEEDED: {data['env']['verify']}",
                detection=f"REJECTED: {reason}",
                rejected=rejected,
            )

        return _output(
            patch,
            style,
            build=f"SUCCEEDED: {data['env']['build']}",
            test=f"SUCCEEDED: {data['env']['verify']}",
            detection="SUCCEEDED: no MISSING findings remain; REDUNDANT findings preserved",
            rejected=rejected,
        )
    finally:
        remove_sandbox(sandbox)
