"""Compose DRAFT's build, repair, selection and verification stages."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from .candidate import CandidateError, apt_mirror_variant, extract_candidates, history_digest, prepare_candidate, select_candidate
from .docker import DEFAULT_BUILD_TIMEOUT, DockerError, Runner, build_image, clean_log, remove_image
from .llm import LLMClient, LLMError, display_endpoint
from .prompt import PROMPT_VERSION, build_choice_prompt, build_context_prompt, build_error_prompt
from .result import (
    RESULT_SCHEMA_VERSION, RoundRecord, RunPaths, new_run_dir, prepare_project,
    write_result, write_round,
)
from .verify import DEFAULT_VERIFY_TIMEOUT, VerifyResult, run_image

_APT_GET_RE = re.compile(r"Get:\d+\s+\S+\s+\[([0-9.]+)\s*([kKMGT]?B)\]", re.IGNORECASE)
_APT_NEED_RE = re.compile(r"Need to get\s+([0-9,.]+)\s*([kKMGT]?B)", re.IGNORECASE)
_APT_FETCHED_RE = re.compile(r"Fetched\s+([0-9,.]+)\s*([kKMGT]?B)", re.IGNORECASE)
_APT_PACKAGE_RE = re.compile(
    r"(?:Setting up|Unpacking)\s+([A-Za-z0-9+.-]+)(?::[A-Za-z0-9-]+)?\s+\(",
    re.IGNORECASE,
)
_BUILD_STEP_RE = re.compile(r"^#\d+\s+\[(\d+/\d+)\]")
_BYTE_UNITS = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}


def _to_bytes(amount: str, unit: str) -> float:
    try:
        value = float(amount.replace(",", ""))
    except ValueError:
        return 0.0
    return value * _BYTE_UNITS.get(unit.lower(), 1)


@dataclass(frozen=True)
class RepairOutcome:
    status: str
    error_code: str | None
    message: str
    paths: RunPaths
    result_path: Path
    rounds: tuple[RoundRecord, ...]
    image_id: str | None
    verify: VerifyResult | None

    def ok(self) -> bool:
        return self.status == "SUCCEEDED"


class SavedCandidates:
    """Serve saved Dockerfiles through the same loop without a network call."""

    prompt_mode = "skipped"
    model = "saved-candidates"

    def __init__(self, files):
        self.files = tuple(Path(file).expanduser().resolve() for file in files)
        if not self.files:
            raise ValueError("no saved Dockerfiles found")
        self.replies = [f"```Dockerfile\n{file.read_text(encoding='utf-8')}\n```\n" for file in self.files]
        self.cursor = 0

    def chat(self, messages, *, timeout=None):
        reply = self.replies[min(self.cursor, len(self.replies) - 1)]
        self.cursor += 1
        return SimpleNamespace(text=reply)


def load_repair_llm(*, env_file=None, candidate=None):
    """Resolve a CLI reply source; saved candidates never read LLM settings."""
    if candidate is not None:
        return SavedCandidates([candidate])
    available_env = env_file if env_file is not None and Path(env_file).is_file() else None
    return LLMClient.from_env(env_file=available_env)


def _stamp(now=None):
    value = now() if callable(now) else now
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(target: Path, text: str):
    target.write_text(text, encoding="utf-8", newline="")


def _fixture_stats(path: Path) -> tuple[int, int]:
    """Count fixture files and bytes, matching prepare_project's exclusions."""
    count = 0
    total = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [name for name in dirs if name not in {".git", ".venv", "__pycache__"}]
        for name in files:
            if name == ".env" or name.startswith(".env."):
                continue
            item = Path(root) / name
            try:
                total += item.stat().st_size
            except OSError:
                pass
            count += 1
    return count, total


def _release_image(build, *, docker: str, runner: Runner | None) -> None:
    """Best-effort image cleanup; evidence keeps only the recorded image ID."""
    tag = getattr(build, "image_tag", None)
    if not tag:
        return
    try:
        remove_image(tag, docker=docker, runner=runner)
    except Exception:
        pass


def _llm_chooser(llm, failure_log: str):
    """Build the R4 chooser; saved candidates have no model to ask."""
    if isinstance(llm, SavedCandidates):
        return None

    def choose(remaining):
        prompt = build_choice_prompt(remaining, failure_log)
        reply = llm.chat([{"role": "user", "content": prompt}]).text
        match = re.search(r"\b([1-9][0-9]*)\b", reply)
        if not match:
            raise CandidateError("R4: model did not return a candidate index")
        return int(match.group(1))

    return choose


def _error_terms(log: str) -> list[str]:
    # Extract concrete dependency/package terms, not generic words such as 'error'.
    terms = []
    for pattern in (
        r"(?:/bin/sh:\s*\d+:\s*)?([\w.+-]+): (?:command )?not found",
        r"unable to locate package\s+([\w.+-]+)",
        r"([\w.+-]+): no such file or directory",
        r"([\w.+-]+)\s+is not specified",
        r"no matching distribution found for\s+([\w.+-]+)",
        r"failed to execute\s+([\w.+-]+)",
        r"file not found[:\s]+([\w./+-]+)",
    ):
        terms.extend(re.findall(pattern, log, re.IGNORECASE))
    return list(dict.fromkeys(term.lower() for term in terms if term.strip()))


def _build_record(build, paths, directory):
    log = directory / "build.log"
    _text(log, clean_log(build.log))
    _text(directory / "build-stdout.log", clean_log(build.stdout))
    _text(directory / "build-stderr.log", clean_log(build.stderr))
    _text(directory / "build-exit-code.txt", f"{build.exit_code}\n")
    return {
        "command": build.command, "exit_code": build.exit_code,
        "failure_class": build.failure_class, "failed_step": build.failed_step,
        "timed_out": build.timed_out, "duration_s": build.duration_s,
        "image_id": build.image_id, "image_tag": build.image_tag,
        "started_at": build.started_at, "finished_at": build.finished_at,
        "log": log.relative_to(paths.root).as_posix(),
    }


def _verify_record(verify, paths, directory):
    # stdout is the verification evidence; stderr is kept separately.
    _text(directory / "verify.log", clean_log(verify.stdout))
    _text(directory / "verify-stderr.log", clean_log(verify.stderr))
    record = asdict(verify)
    record.pop("stdout")
    record.pop("stderr")
    record["log"] = (directory / "verify.log").relative_to(paths.root).as_posix()
    record["stderr_log"] = (directory / "verify-stderr.log").relative_to(paths.root).as_posix()
    return record


def run_repair(
    fixture_dir: Path | str, *, out_dir: Path | str | None = None, llm,
    initial_dockerfile: str = "Dockerfile.broken", prompt_mode: str = "context",
    docker: str = "docker", max_iterations: int = 3,
    build_timeout: float = DEFAULT_BUILD_TIMEOUT, verify_timeout: float = DEFAULT_VERIFY_TIMEOUT,
    expected_output: str = "hello E3", no_cache: bool = False, chooser=None,
    runner: Runner | None = None, now=None, progress: Callable[[dict], None] | None = None,
    apt_mirror: str | None = None,
) -> RepairOutcome:
    fixture_arg = str(fixture_dir)
    fixture = Path(fixture_dir).expanduser().resolve()
    apt_mirror = apt_mirror.strip() if isinstance(apt_mirror, str) else apt_mirror
    apt_mirror = apt_mirror or None

    def emit(event: str, **fields) -> None:
        if progress is None:
            return
        try:
            progress({"event": event, **fields})
        except Exception:
            pass

    def build_with_progress(event: str, iteration: int, **kwargs):
        if progress is None:
            return build_image(**kwargs)
        stop = threading.Event()
        started = time.monotonic()
        counters = {"packages": 0, "bytes": 0.0, "seen_packages": set()}

        def on_line(line: str) -> None:
            text = clean_log(line).strip()
            if not text:
                return
            payload = re.sub(r"^#\d+\s+[0-9.]+\s+", "", text)
            match = _APT_GET_RE.search(payload)
            if match:
                counters["packages"] += 1
                counters["bytes"] += _to_bytes(match.group(1), match.group(2))
                emit(
                    "apt_get", iteration=iteration,
                    packages=counters["packages"],
                    bytes=round(counters["bytes"], 1),
                )
                return
            match = _APT_NEED_RE.search(payload)
            if match:
                emit("apt_total", iteration=iteration, bytes=round(_to_bytes(match.group(1), match.group(2)), 1))
                return
            match = _APT_FETCHED_RE.search(payload)
            if match:
                emit("apt_fetched", iteration=iteration, bytes=round(_to_bytes(match.group(1), match.group(2)), 1))
                return
            match = _APT_PACKAGE_RE.search(payload)
            if match:
                package = match.group(1)
                if package in counters["seen_packages"]:
                    return
                counters["seen_packages"].add(package)
                emit("apt_package", iteration=iteration, package=package)
                return
            match = _BUILD_STEP_RE.match(text)
            if match:
                emit("build_step", iteration=iteration, step=match.group(1))

        def beat() -> None:
            while not stop.wait(15):
                emit(
                    event, iteration=iteration, status="running",
                    elapsed_s=round(time.monotonic() - started, 1),
                )

        worker = threading.Thread(target=beat, daemon=True)
        worker.start()
        try:
            return build_image(**kwargs, on_line=on_line)
        finally:
            stop.set()
            worker.join(timeout=1)

    source = (fixture / initial_dockerfile).resolve()
    if not fixture.is_dir() or not source.is_file():
        raise ValueError(f"fixture directory or initial Dockerfile not found: {source}")
    if not source.is_relative_to(fixture):
        raise ValueError("initial Dockerfile must be inside the fixture directory")
    if prompt_mode not in {"error", "context"}:
        raise ValueError("prompt mode must be error or context")
    if not isinstance(max_iterations, int) or isinstance(max_iterations, bool) or max_iterations <= 0:
        raise ValueError("max_iterations must be a positive integer")
    if any(not math.isfinite(value) or value <= 0 for value in (build_timeout, verify_timeout)):
        raise ValueError("timeouts must be finite and greater than zero")
    if not docker.strip():
        raise ValueError("docker executable must not be empty")
    if apt_mirror and not re.fullmatch(r"[A-Za-z0-9.-]+", apt_mirror):
        raise ValueError("apt mirror must be a hostname, for example repo.huaweicloud.com")
    baseline = source.read_text(encoding="utf-8")
    readme_path = fixture / "README.md"
    readme = readme_path.read_text(encoding="utf-8") if readme_path.is_file() else ""
    if out_dir is None:
        work_root = Path.cwd() / "work"
        if work_root.resolve().is_relative_to(fixture):
            raise ValueError("output directory must be outside the fixture directory")
        paths = new_run_dir(work_root, now=now)
    else:
        root = Path(out_dir).expanduser().resolve()
        if root.is_relative_to(fixture) or fixture.is_relative_to(root):
            raise ValueError("output directory must be separate from the fixture directory")
        root.mkdir(parents=True, exist_ok=False)
        paths = RunPaths(root, root / "project")
    prepare_project(paths, fixture)
    current_file = paths.project / "Dockerfile"
    _text(current_file, baseline)
    started_at, started = _stamp(now), time.monotonic()
    config = getattr(llm, "config", None)
    mode = getattr(llm, "prompt_mode", prompt_mode)
    file_count, total_bytes = _fixture_stats(fixture)
    payload = {
        "schema_version": RESULT_SCHEMA_VERSION, "service": "draft", "run_id": paths.root.name,
        "trace_id": "trace-" + paths.root.name.lower(),
        "status": "FAILED", "error": None, "message": "Repair did not complete",
        "input": {
            "fixture": fixture_arg, "initial_dockerfile": initial_dockerfile,
            "initial_sha256": hashlib.sha256(baseline.encode("utf-8")).hexdigest(),
            "file_count": file_count, "total_bytes": total_bytes,
            "prompt_mode": mode, "prompt_version": PROMPT_VERSION,
            "max_iterations": max_iterations, "expected_output": expected_output,
            "docker": docker, "build_timeout": build_timeout,
            "verify_timeout": verify_timeout, "no_cache": no_cache,
            "apt_mirror": apt_mirror,
            "model": getattr(config, "model", getattr(llm, "model", None)),
            "endpoint": display_endpoint(config.endpoint) if config else None,
        },
        "initial": None, "rounds": [], "final": {"image_id": None, "verify": None},
        "timing": {"started_at": started_at, "finished_at": None, "duration_s": 0.0},
    }
    rounds = []
    final_image = final_verify = None
    container_prefix = "draft-" + hashlib.sha256(str(paths.root).encode("utf-8")).hexdigest()[:16]

    def finish(status, code, message):
        payload.update(status=status, message=message)
        payload["error"] = {"code": code, "message": message, "details": {}} if status != "SUCCEEDED" else None
        payload["timing"].update(finished_at=_stamp(now), duration_s=round(time.monotonic() - started, 3))
        result_path = write_result(paths, payload)
        shutil.rmtree(paths.project, ignore_errors=True)
        emit("done", status=status, error_code=code, rounds=len(rounds))
        return RepairOutcome(status, code, message, paths, result_path, tuple(rounds), final_image, final_verify)

    def verify_image(build, directory, name):
        nonlocal final_image, final_verify
        final_image = build.image_id
        final_verify = run_image(
            build.image_id, expected_output=expected_output, container_name=name,
            cidfile=directory / "verify-container-id.txt", docker=docker,
            timeout=verify_timeout, runner=runner,
        )
        record = _verify_record(final_verify, paths, directory)
        payload["final"] = {"image_id": final_image, "verify": record}
        return record

    write_result(paths, payload)
    current = baseline
    used = {history_digest(baseline)}
    last_failure = ""
    try:
        # The first repair prompt must be grounded in an actual failing build.
        initial_dir = paths.root / "initial"
        initial_dir.mkdir()
        _text(initial_dir / "Dockerfile", baseline)
        emit("initial_build", status="start")
        initial = build_with_progress("initial_build", 0, context_dir=paths.project, docker=docker, timeout=build_timeout, no_cache=no_cache, runner=runner, now=now)
        emit(
            "initial_build", status="end", exit_code=initial.exit_code,
            failure_class=initial.failure_class, timed_out=initial.timed_out,
        )
        payload["initial"] = {
            "build": {
                **_build_record(initial, paths, initial_dir),
                "dockerfile": "initial/Dockerfile",
            },
            "verify": None,
            "evidence": {"dockerfile": "initial/Dockerfile"},
        }
        last_failure = initial.log
        write_result(paths, payload)
        if initial.timed_out:
            _release_image(initial, docker=docker, runner=runner)
            return finish("TIMED_OUT", "EXEC_4002", "Initial build timed out")
        if not initial.ok() and initial.failure_class != "MISSING_MAKE":
            _release_image(initial, docker=docker, runner=runner)
            return finish(
                "FAILED", "ENV_3002",
                f"Initial build failed with {initial.failure_class}; expected MISSING_MAKE",
            )
        if initial.ok():
            emit("initial_verify", status="start")
            payload["initial"]["verify"] = verify_image(initial, initial_dir, f"{container_prefix}-initial")
            emit(
                "initial_verify", status="end", exit_code=final_verify.exit_code,
                output_matches=final_verify.output_matches, timed_out=final_verify.timed_out,
            )
            if final_verify.timed_out:
                _release_image(initial, docker=docker, runner=runner)
                return finish("TIMED_OUT", "EXEC_4002", "Initial verification timed out")
            if final_verify.ok():
                _release_image(initial, docker=docker, runner=runner)
                return finish("SUCCEEDED", None, "Initial Dockerfile already builds and verifies")
            last_failure = f"Verification failed: expected {expected_output!r}, actual {final_verify.stdout!r}, exit_code={final_verify.exit_code}\n{final_verify.stderr}"
        _release_image(initial, docker=docker, runner=runner)

        last_code, last_message = "ENV_3002", "No usable repair candidate"
        for iteration in range(1, max_iterations + 1):
            emit("iteration", iteration=iteration, status="start")
            directory = paths.iter_dir(iteration)
            prompt = (build_context_prompt(current, last_failure, readme) if prompt_mode == "context"
                      else build_error_prompt(current, last_failure))
            _text(directory / "prompt.txt", prompt)
            evidence = {key: f"iter-{iteration:02d}/{file}" for key, file in (
                ("prompt", "prompt.txt"), ("reply", "reply.txt"), ("candidates", "candidates.json"),
            )}
            selected, rejected = {}, []
            digest = ""
            build_record = {
                "command": [], "exit_code": None, "failure_class": "OTHER",
                "failed_step": None, "timed_out": False, "duration_s": 0.0,
                "image_id": None, "image_tag": None, "started_at": None,
                "finished_at": None, "log": None,
            }
            verify_record = None
            fatal = False
            timed_out = False
            build = None
            try:
                emit("prompt", iteration=iteration, status="start")
                reply = llm.chat([{"role": "user", "content": prompt}]).text
                _text(directory / "reply.txt", reply)
                emit("prompt", iteration=iteration, status="end")
                candidates = extract_candidates(reply)
                valid, checks = [], []
                for candidate in candidates:
                    reasons = prepare_candidate(candidate.text, baseline)
                    checks.append({"index": candidate.index, "sha256": candidate.sha256,
                                   "fenced": candidate.fenced, "valid": not reasons, "reasons": reasons,
                                   "text": candidate.text})
                    if reasons:
                        rejected.append({"index": candidate.index, "reason": "; ".join(reasons)})
                    else:
                        valid.append(candidate)
                # Persist candidates before selection, including all invalid ones.
                _text(directory / "candidates.json", json.dumps(checks, ensure_ascii=False, indent=2) + "\n")
                active_chooser = chooser or _llm_chooser(llm, last_failure)
                selection = select_candidate(
                    valid, used_sha256=used, error_terms=_error_terms(last_failure),
                    chooser=active_chooser, baseline=baseline,
                )
                rejected.extend({"index": index, "reason": reason} for index, reason in selection.rejected)
                selected = {"index": selection.candidate.index, "rule": selection.rule, "detail": selection.detail}
                emit(
                    "selected", iteration=iteration, index=selection.candidate.index,
                    rule=selection.rule,
                )
                current = selection.candidate.text
                digest = selection.candidate.sha256
                used.add(selection.candidate.history_key)
                _text(directory / "Dockerfile", current)
                evidence["dockerfile"] = f"iter-{iteration:02d}/Dockerfile"
                _text(current_file, current)
                build_dockerfile = "Dockerfile"
                variant = apt_mirror_variant(current, apt_mirror) if apt_mirror else None
                if variant:
                    build_dockerfile = "Dockerfile.build"
                    _text(directory / build_dockerfile, variant)
                    _text(current_file.parent / build_dockerfile, variant)
                    evidence["build_dockerfile"] = f"iter-{iteration:02d}/{build_dockerfile}"
                else:
                    (current_file.parent / "Dockerfile.build").unlink(missing_ok=True)
                emit("build", iteration=iteration, status="start")
                build = build_with_progress(
                    "build", iteration, context_dir=paths.project, dockerfile=build_dockerfile,
                    docker=docker, timeout=build_timeout, no_cache=no_cache, runner=runner, now=now,
                )
                build_record = _build_record(build, paths, directory)
                build_record["dockerfile"] = f"iter-{iteration:02d}/{build_dockerfile}"
                timed_out = build.timed_out
                emit(
                    "build", iteration=iteration, status="end", exit_code=build.exit_code,
                    failure_class=build.failure_class, timed_out=build.timed_out,
                )
                if not build.ok():
                    last_failure = build.log
                    last_code, last_message = "ENV_3002", "Candidate build failed or image inspection failed"
                else:
                    emit("verify", iteration=iteration, status="start")
                    verify_record = verify_image(build, directory, f"{container_prefix}-{iteration:02d}")
                    timed_out = final_verify.timed_out
                    emit(
                        "verify", iteration=iteration, status="end",
                        exit_code=final_verify.exit_code,
                        output_matches=final_verify.output_matches,
                        timed_out=final_verify.timed_out,
                    )
                    last_code = None
                    last_message = "Candidate verification failed"
                    last_failure = f"Verification failed: expected {expected_output!r}, actual {final_verify.stdout!r}, exit_code={final_verify.exit_code}\n{final_verify.stderr}"
            except CandidateError as exc:
                last_code, last_message = "ENV_3002", str(exc)
                last_failure += f"\nCandidate selection failed: {exc}"
                build_record["reason"] = str(exc)
                if not (directory / "candidates.json").exists():
                    _text(directory / "candidates.json", "[]\n")
            except (LLMError, DockerError) as exc:
                last_code, last_message, fatal = None, str(exc), True
                build_record["reason"] = str(exc)
            finally:
                if build is not None:
                    _release_image(build, docker=docker, runner=runner)
            evidence = {key: path for key, path in evidence.items() if (paths.root / path).is_file()}
            record = RoundRecord(iteration, digest, selected, rejected, build_record, verify_record, evidence)
            rounds.append(record)
            payload["rounds"].append(asdict(record))
            write_round(paths, record)
            write_result(paths, payload)
            emit(
                "iteration", iteration=iteration, status="end",
                build_exit_code=build_record.get("exit_code"),
                verification_ok=bool(verify_record is not None and final_verify.ok()),
            )
            if fatal:
                return finish("FAILED", last_code, last_message)
            if timed_out:
                return finish("TIMED_OUT", "EXEC_4002", "Candidate build or verification timed out")
            if verify_record is not None and final_verify.ok():
                return finish("SUCCEEDED", None, "Candidate built and verified successfully")
        return finish("FAILED", last_code, f"Iteration limit ({max_iterations}) reached: {last_message}")
    except DockerError as exc:
        return finish("FAILED", None, str(exc))
