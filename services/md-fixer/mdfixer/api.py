"""HTTP service for contract-driven REPAIR jobs."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import logging.handlers
import os
import queue
import signal
import shutil
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote
from uuid import uuid4

from . import SERVICE, __version__
from .job_store import IdempotencyConflict, JobStore
from .service_runner import ServiceError, ValidationError, run_contract_repair, validate_request

from draft.llm import DEFAULT_TIMEOUT, LLMClient, LLMConfig, LLMConfigError

MAX_BODY_BYTES = 1024 * 1024
JOB_RETENTION = timedelta(hours=24)
CLEANUP_INTERVAL = 3600
LOGGER = logging.getLogger("mdfixer.api")
_JOB_ERROR_CODES = {
    "ENV_3002",
    "EXEC_4002",
    "REPAIR_6000",
    "REPAIR_6001",
    "REPAIR_6002",
    "REPAIR_6003",
    "REPAIR_6004",
    "REPAIR_6005",
}


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _llm_from_headers(headers: Mapping[str, str]) -> LLMConfig | None:
    api_url = (headers.get("X-LLM-Api-Url") or "").strip()
    api_key = (headers.get("X-LLM-Api-Key") or "").strip()
    model = (headers.get("X-LLM-Model") or "").strip()
    present = [bool(api_url), bool(api_key), bool(model)]
    if not any(present):
        return None
    if not all(present):
        raise LLMConfigError("LLM headers must include X-LLM-Api-Url, X-LLM-Api-Key and X-LLM-Model")
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


class MdfixerService:
    def __init__(self, data_dir: Path, *, artifact_base_url: str | None = None) -> None:
        self.data_dir = data_dir.resolve()
        self.artifact_base_url = (artifact_base_url or "").strip() or None
        self.jobs_dir = self.data_dir / "jobs"
        self.logs_dir = self.data_dir / "logs"
        self.artifacts_dir = self.data_dir / "artifacts"
        for path in (self.jobs_dir, self.logs_dir, self.artifacts_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.data_dir / "jobs.db")
        self.queue: queue.Queue[tuple[str, LLMConfig | None] | None] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = threading.Thread(target=self._work, name="mdfixer-worker", daemon=True)
        self.cleaner = threading.Thread(target=self._cleanup_loop, name="mdfixer-cleaner", daemon=True)
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
                llm = LLMClient(config) if config is not None else None
                output = run_contract_repair(
                    job["input"],
                    llm,
                    job_id,
                    work_dir,
                    self.data_dir,
                    artifact_base_url=self.artifact_base_url,
                )
                self.store.succeed(job_id, output)
                LOGGER.info("succeeded %s", job_id)
            except ServiceError as exc:
                code = exc.code if exc.code in _JOB_ERROR_CODES else "REPAIR_6000"
                status = "TIMED_OUT" if code == "EXEC_4002" else "FAILED"
                self.store.fail(job_id, code, str(exc), details=exc.details, status=status)
                LOGGER.warning("failed %s: %s %s", job_id, code, exc)
            except Exception as exc:  # noqa: BLE001 - keep the worker alive
                self.store.fail(job_id, "REPAIR_6000", f"internal error: {exc}")
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
                shutil.rmtree(work_dir, ignore_errors=True)
        if removed:
            LOGGER.info("cleaned %d expired job(s)", len(removed))
        return len(removed)

    def artifact_path(self, pair: str, job: str, name: str) -> Path:
        if pair != "pair07":
            raise PermissionError("pair is not readable")
        parts = (pair, job, name)
        if any(not part or part in {".", ".."} or "/" in part or "\\" in part for part in parts):
            raise FileNotFoundError("artifact not found")
        path = (self.artifacts_dir / pair / job / name).resolve()
        try:
            path.relative_to(self.artifacts_dir)
        except ValueError as exc:
            raise FileNotFoundError("artifact not found") from exc
        if not path.is_file():
            raise FileNotFoundError("artifact not found")
        return path


class Handler(BaseHTTPRequestHandler):
    service: MdfixerService
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
            self._send_json(200, {"status": "ok", "service": SERVICE, "version": __version__})
            return
        if path.startswith("/v1/artifacts/"):
            parts = [unquote(part) for part in path.removeprefix("/v1/artifacts/").split("/")]
            if len(parts) != 3:
                self._error(404, "artifact not found", "NOT_FOUND")
                return
            try:
                artifact = self.service.artifact_path(*parts)
            except PermissionError:
                self._error(403, "artifact access denied", "FORBIDDEN")
                return
            except FileNotFoundError:
                self._error(404, "artifact not found", "NOT_FOUND")
                return
            raw = artifact.read_bytes()
            self.send_response(200)
            content_type = "application/json; charset=utf-8" if artifact.suffix == ".json" else "application/octet-stream"
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Artifact-Sha256", hashlib.sha256(raw).hexdigest())
            self.end_headers()
            self.wfile.write(raw)
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
        if self.path.split("?", 1)[0] != "/v1/repair-jobs":
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
            self._error(500, f"internal error: {exc}", "REPAIR_6000")
            return
        self._send_json(202, job)


def _handler(service: MdfixerService) -> type[Handler]:
    return type("MdfixerHandler", (Handler,), {"service": service})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mdfixer-api", description="MDFixer HTTP service.")
    parser.add_argument("--host", default=os.environ.get("MDFIXER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MDFIXER_PORT", "8081")))
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("MDFIXER_DATA_DIR", "/data"),
        help="job, artifact and log directory",
    )
    parser.add_argument(
        "--artifact-base-url",
        default=os.environ.get("MDFIXER_ARTIFACT_BASE_URL", ""),
        help="HTTP prefix for artifact:// resolution, for example http://127.0.0.1:8080/v1/artifacts",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = MdfixerService(Path(args.data_dir), artifact_base_url=args.artifact_base_url)
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
