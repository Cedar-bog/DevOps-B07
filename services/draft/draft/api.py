"""HTTP service for contract-driven DRAFT jobs."""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import queue
import signal
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from . import __version__
from .job_store import IdempotencyConflict, JobStore
from .llm import DEFAULT_TIMEOUT, LLMClient, LLMConfig, LLMConfigError
from .service_runner import ServiceError, ValidationError, run_contract_repair, validate_request

MAX_BODY_BYTES = 1024 * 1024
JOB_RETENTION = timedelta(hours=24)
CLEANUP_INTERVAL = 3600
LOGGER = logging.getLogger("draft.api")


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _llm_from_headers(headers: Mapping[str, str]) -> LLMConfig:
    api_url = (headers.get("X-LLM-Api-Url") or "").strip()
    api_key = (headers.get("X-LLM-Api-Key") or "").strip()
    model = (headers.get("X-LLM-Model") or "").strip()
    missing = [
        name for name, value in (
            ("X-LLM-Api-Url", api_url),
            ("X-LLM-Api-Key", api_key),
            ("X-LLM-Model", model),
        ) if not value
    ]
    if missing:
        raise LLMConfigError(f"missing headers: {', '.join(missing)}")
    try:
        timeout = float(headers.get("X-LLM-Timeout") or DEFAULT_TIMEOUT)
        max_tokens_value = headers.get("X-LLM-Max-Tokens")
        max_tokens = int(max_tokens_value) if max_tokens_value else None
        temperature_value = headers.get("X-LLM-Temperature")
        temperature = float(temperature_value) if temperature_value else None
    except ValueError as exc:
        raise LLMConfigError(f"invalid LLM header: {exc}") from exc
    return LLMConfig(
        api_url=api_url,
        api_key=api_key,
        model=model,
        timeout=timeout,
        max_tokens=max_tokens,
        temperature=temperature,
    )


class DraftService:
    def __init__(self, data_dir: Path, *, docker: str = "docker") -> None:
        self.data_dir = data_dir.resolve()
        self.jobs_dir = self.data_dir / "jobs"
        self.logs_dir = self.data_dir / "logs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.data_dir / "jobs.db")
        self.docker = docker
        self.queue: queue.Queue[tuple[str, LLMConfig] | None] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = threading.Thread(target=self._work, name="draft-worker", daemon=True)
        self.cleaner = threading.Thread(target=self._cleanup_loop, name="draft-cleaner", daemon=True)
        self._setup_logging()
        recovered = self.store.recover_interrupted()
        if recovered:
            LOGGER.warning("marked %d interrupted job(s) as failed", recovered)

    def _setup_logging(self) -> None:
        if LOGGER.handlers:
            return
        LOGGER.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        LOGGER.addHandler(stream)
        file_handler = logging.handlers.TimedRotatingFileHandler(
            self.logs_dir / "service.log",
            when="midnight",
            backupCount=7,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        LOGGER.addHandler(file_handler)

    def start(self) -> None:
        self.worker.start()
        self.cleaner.start()

    def stop(self) -> None:
        self.stop_event.set()
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass

    def submit(self, payload: Mapping[str, Any], headers: Mapping[str, str]) -> dict[str, Any]:
        data = validate_request(payload)
        config = _llm_from_headers(headers)
        job_id = f"job-{uuid4().hex}"
        work_dir = self.jobs_dir / job_id
        job = self.store.create(
            str(payload["trace_id"]),
            data,
            data["idempotency_key"],
            work_dir,
            runner=self.store.runner_name(),
            job_id=job_id,
        )
        if job["job_id"] == job_id:
            self.queue.put((job_id, config))
            LOGGER.info("queued %s", job_id)
        else:
            LOGGER.info("returned existing job %s for idempotency key", job["job_id"])
        return job

    def _work(self) -> None:
        while not self.stop_event.is_set():
            try:
                item = self.queue.get(timeout=1)
            except queue.Empty:
                continue
            if item is None:
                break
            job_id, config = item
            job = self.store.get(job_id)
            if job is None or job["status"] != "QUEUED":
                continue
            self.store.mark_running(job_id)
            work_dir = self.jobs_dir / job_id
            LOGGER.info("started %s", job_id)
            try:
                outcome = run_contract_repair(
                    job["input"],
                    LLMClient(config),
                    job_id,
                    work_dir,
                    docker=self.docker,
                )
                self.store.succeed(job_id, outcome.output)
                LOGGER.info("succeeded %s", job_id)
            except ServiceError as exc:
                code = exc.code if exc.code in {"ENV_3002", "EXEC_4002", "ANALYSIS_5001"} else "ANALYSIS_5001"
                status = "TIMED_OUT" if code == "EXEC_4002" else "FAILED"
                self.store.fail(job_id, code, str(exc), details=exc.details, status=status)
                LOGGER.warning("failed %s: %s %s", job_id, code, exc)
            except Exception as exc:  # noqa: BLE001 - keep the worker alive
                self.store.fail(job_id, "ANALYSIS_5001", f"internal error: {exc}")
                LOGGER.exception("internal error for %s", job_id)

    def _cleanup_loop(self) -> None:
        while not self.stop_event.wait(CLEANUP_INTERVAL):
            self.cleanup()

    def cleanup(self) -> int:
        cutoff = (
            datetime.now(timezone.utc) - JOB_RETENTION
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        removed = self.store.cleanup(cutoff)
        for item in removed:
            work_dir = item.get("work_dir")
            if work_dir:
                import shutil

                shutil.rmtree(work_dir, ignore_errors=True)
            self._remove_images(str(item.get("job_id")), item.get("output"))
        if removed:
            LOGGER.info("cleaned %d expired job(s)", len(removed))
        return len(removed)

    def _remove_images(self, job_id: str, output: object) -> None:
        prefix = f"b07-draft:{job_id}-"
        exact = output.get("image_ref") if isinstance(output, dict) else None
        tags = subprocess.run(
            [self.docker, "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if tags.returncode != 0:
            return
        for tag in tags.stdout.splitlines():
            if tag.startswith(prefix) or (exact and tag == str(exact)):
                subprocess.run(
                    [self.docker, "rmi", "-f", tag],
                    capture_output=True,
                    check=False,
                )


class Handler(BaseHTTPRequestHandler):
    service: DraftService

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.info("%s %s", self.address_string(), fmt % args)

    def _send_json(self, status: int, payload: Mapping[str, Any]) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str, code: str = "INVALID_INPUT") -> None:
        self._send_json(status, {"error": {"code": code, "message": message}})

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._send_json(200, {"status": "ok", "service": "draft", "version": __version__})
            return
        if path.startswith("/v1/jobs/"):
            job_id = path.removeprefix("/v1/jobs/")
            if not job_id or "/" in job_id:
                self._error(404, "job not found", "NOT_FOUND")
                return
            job = self.service.store.get(job_id)
            if job is None:
                self._error(404, "job not found", "NOT_FOUND")
                return
            self._send_json(200, job)
            return
        self._error(404, "not found", "NOT_FOUND")

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/v1/dockerfile-jobs":
            self._error(404, "not found", "NOT_FOUND")
            return
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else 0
        except ValueError:
            self._error(400, "invalid Content-Length")
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._error(413, "request body is too large or empty", "INVALID_INPUT")
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self._error(400, "request body must be valid JSON")
            return
        try:
            job = self.service.submit(payload, self.headers)
        except ValidationError as exc:
            self._error(400, str(exc))
            return
        except LLMConfigError as exc:
            self._error(400, str(exc), "LLM_CONFIG")
            return
        except IdempotencyConflict as exc:
            self._error(409, str(exc), "IDEMPOTENCY_CONFLICT")
            return
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("submit failed")
            self._error(500, f"internal error: {exc}", "ANALYSIS_5001")
            return
        self._send_json(202, job)


def _handler(service: DraftService) -> type[Handler]:
    return type("DraftHandler", (Handler,), {"service": service})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="draft-api", description="DRAFT HTTP service.")
    parser.add_argument("--host", default=os.environ.get("DRAFT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DRAFT_PORT", "8080")))
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("DRAFT_DATA_DIR", "/data"),
        help="job, artifact and log directory",
    )
    parser.add_argument("--docker", default=os.environ.get("DRAFT_DOCKER", "docker"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = DraftService(Path(args.data_dir), docker=args.docker)
    service.start()
    server = ThreadingHTTPServer((args.host, args.port), _handler(service))
    server.daemon_threads = True

    def stop(_signum, _frame) -> None:
        service.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    LOGGER.info("listening on %s:%d", args.host, args.port)
    try:
        server.serve_forever()
    finally:
        service.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
