"""SQLite-backed asynchronous job store for the DRAFT service."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

SCHEMA_VERSION = "1.0"
JOB_TYPE = "REPAIR"


class IdempotencyConflict(RuntimeError):
    """The same idempotency key was used with a different input."""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def input_hash(data: Mapping[str, Any]) -> str:
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class JobStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs
                (
                    job_id
                    TEXT
                    PRIMARY
                    KEY,
                    trace_id
                    TEXT
                    NOT
                    NULL,
                    job_type
                    TEXT
                    NOT
                    NULL,
                    status
                    TEXT
                    NOT
                    NULL,
                    created_at
                    TEXT
                    NOT
                    NULL,
                    queued_at
                    TEXT,
                    started_at
                    TEXT,
                    finished_at
                    TEXT,
                    input_json
                    TEXT
                    NOT
                    NULL,
                    output_json
                    TEXT,
                    error_json
                    TEXT,
                    idempotency_key
                    TEXT
                    NOT
                    NULL
                    UNIQUE,
                    input_hash
                    TEXT
                    NOT
                    NULL,
                    work_dir
                    TEXT,
                    runner
                    TEXT
                    NOT
                    NULL
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status)")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_finished ON jobs(finished_at)")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def _job(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "job_id": row["job_id"],
            "trace_id": row["trace_id"],
            "job_type": row["job_type"],
            "status": row["status"],
            "execution": {
                "created_at": row["created_at"],
                "queued_at": row["queued_at"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "attempts": 1,
                "runner": row["runner"],
            },
            "input": json.loads(row["input_json"]),
            "output": json.loads(row["output_json"]) if row["output_json"] else None,
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
        }

    def _find_by_key(self, db: sqlite3.Connection, key: str) -> sqlite3.Row | None:
        return db.execute(
            "SELECT * FROM jobs WHERE idempotency_key = ?", (key,)
        ).fetchone()

    def create(
            self,
            trace_id: str,
            data: Mapping[str, Any],
            idempotency_key: str,
            work_dir: Path,
            *,
            runner: str = "draft-service",
            job_id: str | None = None,
    ) -> dict[str, Any]:
        digest = input_hash(data)
        with self._lock, self._connect() as db:
            existing = self._find_by_key(db, idempotency_key)
            if existing is not None:
                if existing["input_hash"] != digest:
                    raise IdempotencyConflict(
                        "idempotency_key was already used with a different input"
                    )
                return self._job(existing)

            job_id = job_id or f"job-{uuid4().hex}"
            created = now_iso()
            db.execute(
                """
                INSERT INTO jobs (job_id, trace_id, job_type, status, created_at, queued_at,
                                  input_json, idempotency_key, input_hash, work_dir, runner)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    trace_id,
                    JOB_TYPE,
                    "QUEUED",
                    created,
                    created,
                    json.dumps(data, ensure_ascii=False, sort_keys=True),
                    idempotency_key,
                    digest,
                    str(work_dir),
                    runner,
                ),
            )
            row = db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            return self._job(row)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._job(row) if row is not None else None

    def mark_running(self, job_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "UPDATE jobs SET status = ?, started_at = ? WHERE job_id = ? AND status = ?",
                ("RUNNING", now_iso(), job_id, "QUEUED"),
            )

    def succeed(self, job_id: str, output: Mapping[str, Any]) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                """
                UPDATE jobs
                SET status      = ?,
                    output_json = ?,
                    error_json  = NULL,
                    finished_at = ?
                WHERE job_id = ?
                """,
                ("SUCCEEDED", json.dumps(output, ensure_ascii=False, sort_keys=True), now_iso(), job_id),
            )

    def fail(
            self,
            job_id: str,
            code: str,
            message: str,
            *,
            details: Mapping[str, Any] | None = None,
            status: str = "FAILED",
    ) -> None:
        error = {"code": code, "message": message, "details": dict(details or {})}
        with self._lock, self._connect() as db:
            db.execute(
                """
                UPDATE jobs
                SET status      = ?,
                    error_json  = ?,
                    output_json = NULL,
                    finished_at = ?
                WHERE job_id = ?
                """,
                (status, json.dumps(error, ensure_ascii=False, sort_keys=True), now_iso(), job_id),
            )

    def recover_interrupted(self) -> int:
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT job_id FROM jobs WHERE status IN ('QUEUED', 'RUNNING')"
            ).fetchall()
            for row in rows:
                error = {
                    "code": "REPAIR_6000",
                    "message": "service restarted before the job finished",
                    "details": {},
                }
                db.execute(
                    """
                    UPDATE jobs
                    SET status      = ?,
                        error_json  = ?,
                        finished_at = ?
                    WHERE job_id = ?
                    """,
                    ("FAILED", json.dumps(error, ensure_ascii=False), now_iso(), row["job_id"]),
                )
        return len(rows)

    def cleanup(self, cutoff_iso: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT job_id, work_dir, output_json FROM jobs WHERE finished_at IS NOT NULL AND finished_at < ?",
                (cutoff_iso,),
            ).fetchall()
            for row in rows:
                db.execute("DELETE FROM jobs WHERE job_id = ?", (row["job_id"],))
        return [
            {
                "job_id": row["job_id"],
                "work_dir": row["work_dir"],
                "output": json.loads(row["output_json"]) if row["output_json"] else None,
            }
            for row in rows
        ]

    def runner_name(self) -> str:
        return os.uname().nodename if hasattr(os, "uname") else "draft-service"
