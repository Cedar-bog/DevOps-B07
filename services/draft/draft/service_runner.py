"""Contract-driven DRAFT runner for the HTTP service.

This module does not use the fixture-oriented ``run_repair`` path. It consumes
the shared DRAFT input contract, clones a Git repository, builds and verifies a
Dockerfile, and asks the caller-provided LLM to repair failures.
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
from uuid import uuid4

from .candidate import CandidateError, basic_reasons, extract_candidates, select_candidate
from .docker import (
    CommandResult,
    DockerError,
    Runner,
    build_image,
    clean_log,
    default_runner,
)
from .llm import LLMClient, LLMConfig, LLMError
from .prompt import DEFAULT_KEYWORDS, MAX_README_CHARS, locate_error
from .verify import remove_container

MAX_REPO_BYTES = 500 * 1024 * 1024
MAX_REPO_FILES = 100_000
CLONE_TIMEOUT = 300.0
BUILD_TIMEOUT = 900.0
VERIFY_TIMEOUT = 300.0
DEFAULT_WORKDIR = "/app"
MAX_ROUND_LOG = 12_000
MAX_FILE_TREE = 200
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_TRACE = re.compile(r"^trace-[a-z0-9-]+$")
_INPUT_KEYS = {"repository", "build", "verify", "max_iterations", "time_limit", "idempotency_key"}


class ValidationError(ValueError):
    """The request does not satisfy the shared DRAFT contract."""


class ServiceError(RuntimeError):
    """A job failed after the request was accepted."""

    def __init__(self, code: str, message: str, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True)
class RepairOutcome:
    output: dict[str, Any]
    image_ref: str


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_request(payload: object) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValidationError("request must be a JSON object")
    if set(payload) - {"schema_version", "trace_id", "job_type", "input"}:
        raise ValidationError("unknown request fields")
    if payload.get("schema_version") != "1.0":
        raise ValidationError("schema_version must be 1.0")
    if payload.get("job_type") != "DRAFT":
        raise ValidationError("job_type must be DRAFT")
    trace_id = payload.get("trace_id")
    if not isinstance(trace_id, str) or not _TRACE.fullmatch(trace_id):
        raise ValidationError("trace_id must match ^trace-[a-z0-9-]+$")

    data = payload.get("input")
    if not isinstance(data, Mapping) or set(data) != _INPUT_KEYS:
        raise ValidationError("input must contain exactly the DRAFT fields")

    repository = data.get("repository")
    if not isinstance(repository, Mapping) or set(repository) != {"url", "commit"}:
        raise ValidationError("repository requires url and commit")
    url = repository.get("url")
    if not _nonempty(url):
        raise ValidationError("repository.url is required")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValidationError("repository.url must be an absolute http(s) URL without credentials")
    commit = repository.get("commit")
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise ValidationError("repository.commit must be a full lowercase SHA")

    build = data.get("build")
    if not isinstance(build, Mapping) or "command" not in build or set(build) - {"command", "workdir"}:
        raise ValidationError("build requires command and may contain workdir")
    if not _nonempty(build.get("command")):
        raise ValidationError("build.command is required")
    workdir = build.get("workdir")
    if workdir is not None and not _nonempty(workdir):
        raise ValidationError("build.workdir must be a nonempty string")

    verify = data.get("verify")
    if not isinstance(verify, Mapping) or set(verify) - {"command", "expected_output"}:
        raise ValidationError("verify requires command")
    if not _nonempty(verify.get("command")):
        raise ValidationError("verify.command is required")
    expected_output = verify.get("expected_output")
    if expected_output is not None and not isinstance(expected_output, str):
        raise ValidationError("verify.expected_output must be a string")

    max_iterations = data.get("max_iterations")
    if not _is_int(max_iterations) or max_iterations < 1:
        raise ValidationError("max_iterations must be a positive integer")
    time_limit = data.get("time_limit")
    if not _is_int(time_limit) or time_limit < 1:
        raise ValidationError("time_limit must be a positive integer")
    idempotency_key = data.get("idempotency_key")
    if not _nonempty(idempotency_key):
        raise ValidationError("idempotency_key is required")

    return {
        "repository": {"url": url, "commit": commit},
        "build": {
            "command": build["command"],
            "workdir": workdir or DEFAULT_WORKDIR,
        },
        "verify": {
            "command": verify["command"],
            **({"expected_output": expected_output} if expected_output is not None else {}),
        },
        "max_iterations": max_iterations,
        "time_limit": time_limit,
        "idempotency_key": idempotency_key,
    }


def _ok(result: CommandResult) -> bool:
    return not result.timed_out and result.exit_code == 0


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise ServiceError("EXEC_4002", "time limit exceeded")
    return left


def _run(
    command: list[str],
    *,
    runner: Runner | None,
    timeout: float,
    cwd: Path | None = None,
) -> CommandResult:
    try:
        return (runner or default_runner)(command, cwd or Path.cwd(), timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return CommandResult(126, "", str(exc), 0.0)


def _clone(repository: Mapping[str, str], destination: Path, deadline: float) -> None:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    timeout = min(CLONE_TIMEOUT, _remaining(deadline))
    try:
        clone = subprocess.run(
            ["git", "clone", "--no-checkout", repository["url"], str(destination)],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ServiceError("EXEC_4002", "git clone timed out") from exc
    except OSError as exc:
        raise ServiceError("ANALYSIS_5001", f"cannot start git clone: {exc}") from exc
    if clone.returncode:
        raise ServiceError("CLONE_FAILED", (clone.stderr or clone.stdout).strip() or "git clone failed")

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
            "CLONE_FAILED",
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
            "REPO_TOO_LARGE",
            f"repository exceeds limits: {total} bytes, {files} files",
        )


def _read_readme(repo: Path) -> str:
    for name in ("README.md", "README", "readme.md"):
        path = repo / name
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8", errors="replace")[:MAX_README_CHARS]
            except OSError:
                return ""
    return ""


def _file_tree(repo: Path) -> str:
    entries = []
    for path in sorted(repo.rglob("*")):
        if not path.is_file() or ".git" in path.relative_to(repo).parts:
            continue
        relative = path.relative_to(repo).as_posix()
        if relative in {"Dockerfile", "Dockerfile.draft"}:
            continue
        entries.append(relative)
        if len(entries) >= MAX_FILE_TREE:
            break
    return "\n".join(entries)


def _initial_prompt(repo: Path, data: Mapping[str, Any]) -> str:
    build = data["build"]
    verify = data["verify"]
    return (
        "DRAFT initial Dockerfile\n"
        "根据下面的项目信息生成 Dockerfile，使构建命令和验证命令都能在容器内运行。\n"
        "输出 3 个候选 Dockerfile，每个候选使用独立的 ```Dockerfile 围栏。\n"
        "必须安装构建依赖，设置 WORKDIR，并把项目复制进去。"
        "不要使用多阶段构建，不要在运行阶段访问网络。\n\n"
        f"Build command: {build['command']}\n"
        f"Workdir: {build['workdir']}\n"
        f"Verify command: {verify['command']}\n\n"
        f"File tree:\n{_file_tree(repo)}\n\n"
        f"README:\n{_read_readme(repo)}\n"
    )


def _repair_prompt(current: str, failure_log: str, repo: Path, data: Mapping[str, Any]) -> str:
    build = data["build"]
    verify = data["verify"]
    return (
        "DRAFT repair Dockerfile\n"
        "下面的 Dockerfile 在构建或验证阶段失败。请修复它，使构建命令和验证命令都能通过。\n"
        "输出 3 个候选 Dockerfile，每个候选使用独立的 ```Dockerfile 围栏。\n"
        "保留仍然有效的指令，必要时安装依赖或调整 WORKDIR/COPY。"
        "不要使用多阶段构建，不要在运行阶段访问网络。\n\n"
        f"Build command: {build['command']}\n"
        f"Workdir: {build['workdir']}\n"
        f"Verify command: {verify['command']}\n\n"
        f"Current Dockerfile:\n```Dockerfile\n{current}\n```\n\n"
        f"Failure evidence:\n{locate_error(failure_log).render()}\n\n"
        f"README:\n{_read_readme(repo)}\n"
    )


def _ask_candidates(
    client: LLMClient,
    prompt: str,
    current: str,
    *,
    used: set[str],
    failure_log: str,
    deadline: float,
):
    _remaining(deadline)
    try:
        reply = client.chat(
            [{"role": "user", "content": prompt}],
            timeout=min(120.0, _remaining(deadline)),
        ).content
    except LLMError as exc:
        raise ServiceError("ANALYSIS_5001", f"LLM request failed: {exc}") from exc
    candidates = extract_candidates(reply)
    valid = []
    rejected = []
    for candidate in candidates:
        reasons = basic_reasons(candidate.text)
        if reasons:
            rejected.append({"index": candidate.index, "reason": "; ".join(reasons)})
        else:
            valid.append(candidate)
    if not valid:
        raise ServiceError(
            "ANALYSIS_5001",
            "LLM returned no valid Dockerfile candidates",
            {"rejected": rejected},
        )
    terms = tuple(term for term in DEFAULT_KEYWORDS if term in failure_log.lower())
    try:
        selection = select_candidate(
            valid,
            used_sha256=used,
            error_terms=terms,
            baseline=current or None,
        )
    except CandidateError as exc:
        raise ServiceError("ANALYSIS_5001", str(exc), {"rejected": rejected}) from exc
    used.add(selection.candidate.history_key)
    return selection


def _start_container(
    image_id: str,
    workdir: str,
    *,
    docker: str,
    runner: Runner | None,
    timeout: float,
) -> tuple[str, CommandResult]:
    name = f"b07-draft-{uuid4().hex[:12]}"
    command = [
        docker, "run", "-d", "--name", name,
        "--network", "none",
        "--cpus", "2", "--memory", "1g", "--pids-limit", "256",
        "-w", workdir,
        "--entrypoint", "sh",
        image_id, "-c", "while :; do sleep 3600; done",
    ]
    return name, _run(command, runner=runner, timeout=min(60.0, timeout))


def _exec(
    name: str,
    workdir: str,
    command: str,
    *,
    docker: str,
    runner: Runner | None,
    timeout: float,
) -> CommandResult:
    return _run(
        [docker, "exec", "-w", workdir, name, "sh", "-lc", command],
        runner=runner,
        timeout=timeout,
    )


def _build_verify(
    image_id: str,
    data: Mapping[str, Any],
    *,
    docker: str,
    runner: Runner | None,
    deadline: float,
) -> tuple[bool, str, str]:
    workdir = data["build"]["workdir"]
    timeout = min(VERIFY_TIMEOUT, _remaining(deadline))
    name, started = _start_container(
        image_id, workdir, docker=docker, runner=runner, timeout=timeout
    )
    if not _ok(started):
        return False, "container start failed", clean_log(started.stdout + started.stderr)
    parts = []
    try:
        build = _exec(
            name, workdir, data["build"]["command"],
            docker=docker, runner=runner, timeout=min(BUILD_TIMEOUT, _remaining(deadline)),
        )
        parts.append(f"$ {data['build']['command']}\n{clean_log(build.stdout + build.stderr)}")
        if not _ok(build):
            return False, "build command failed", "\n".join(parts)
        verify = _exec(
            name, workdir, data["verify"]["command"],
            docker=docker, runner=runner, timeout=min(VERIFY_TIMEOUT, _remaining(deadline)),
        )
        parts.append(f"$ {data['verify']['command']}\n{clean_log(verify.stdout + verify.stderr)}")
        if not _ok(verify):
            return False, "verify command failed", "\n".join(parts)
        expected = data["verify"].get("expected_output")
        observed = verify.stdout.rstrip("\r\n")
        if expected is not None and observed != expected:
            return (
                False,
                f"verify output mismatch: expected {expected!r}, got {observed!r}",
                "\n".join(parts),
            )
        return True, "success", "\n".join(parts)
    finally:
        remove_container(name, docker=docker, runner=runner)


def _round_log(text: str) -> str:
    return text[:MAX_ROUND_LOG]


def _write_round_log(work_dir: Path, iteration: int, stage: str, text: str) -> str:
    logs = work_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    relative = Path("logs") / f"round-{iteration:02d}-{stage}.log"
    (work_dir / relative).write_text(text, encoding="utf-8")
    return relative.as_posix()


def run_contract_repair(
    data: Mapping[str, Any],
    client: LLMClient,
    job_id: str,
    work_dir: Path,
    *,
    docker: str = "docker",
    runner: Runner | None = None,
) -> RepairOutcome:
    started = time.monotonic()
    deadline = started + int(data["time_limit"])
    work_dir.mkdir(parents=True, exist_ok=True)
    repo = work_dir / "repo"
    _clone(data["repository"], repo, deadline)

    used: set[str] = set()
    source_dockerfile = repo / "Dockerfile"
    if source_dockerfile.is_file():
        current = source_dockerfile.read_text(encoding="utf-8", errors="replace")
    else:
        selection = _ask_candidates(
            client,
            _initial_prompt(repo, data),
            "",
            used=used,
            failure_log="",
            deadline=deadline,
        )
        current = selection.candidate.text

    rounds: list[dict[str, Any]] = []
    last_failure = ""
    for iteration in range(1, int(data["max_iterations"]) + 1):
        _remaining(deadline)
        dockerfile = repo / "Dockerfile.draft"
        dockerfile.write_text(current, encoding="utf-8")
        tag = f"b07-draft:{job_id}-{iteration}"
        try:
            build = build_image(
                repo,
                dockerfile="Dockerfile.draft",
                docker=docker,
                tag=tag,
                timeout=min(BUILD_TIMEOUT, _remaining(deadline)),
                runner=runner,
            )
        except DockerError as exc:
            raise ServiceError("ENV_3002", str(exc), {"rounds": rounds}) from exc

        if not build.ok():
            last_failure = build.log
            rounds.append({
                "iteration": iteration,
                "log": _round_log(build.log),
                "log_file": _write_round_log(work_dir, iteration, "build", build.log),
                "changes": f"candidate from round {iteration}",
                "rationale": "image build failed; send the log to the LLM",
            })
            if iteration >= int(data["max_iterations"]):
                raise ServiceError("ENV_3002", "image build failed", {"rounds": rounds})
            selection = _ask_candidates(
                client,
                _repair_prompt(current, last_failure, repo, data),
                current,
                used=used,
                failure_log=last_failure,
                deadline=deadline,
            )
            current = selection.candidate.text
            continue

        ok, reason, log = _build_verify(
            build.image_id or "",
            data,
            docker=docker,
            runner=runner,
            deadline=deadline,
        )
        if not ok:
            last_failure = log
            rounds.append({
                "iteration": iteration,
                "log": _round_log(log),
                "log_file": _write_round_log(work_dir, iteration, "verify", log),
                "changes": f"candidate from round {iteration}",
                "rationale": f"{reason}; send the log to the LLM",
            })
            if iteration >= int(data["max_iterations"]):
                raise ServiceError("ENV_3002", reason, {"rounds": rounds})
            selection = _ask_candidates(
                client,
                _repair_prompt(current, last_failure, repo, data),
                current,
                used=used,
                failure_log=last_failure,
                deadline=deadline,
            )
            current = selection.candidate.text
            continue

        rounds.append({
            "iteration": iteration,
            "log": _round_log(log),
            "log_file": _write_round_log(work_dir, iteration, "verify", log),
            "changes": f"candidate from round {iteration}",
            "rationale": "build and verify commands exited with 0",
        })
        return RepairOutcome(
            output={
                "dockerfile": current,
                "image_ref": build.image_tag,
                "rounds": rounds,
                "final_build": "success",
                "final_verify": "verify command exited 0",
            },
            image_ref=build.image_tag,
        )

    raise ServiceError("ENV_3002", "iteration limit reached", {"rounds": rounds})
