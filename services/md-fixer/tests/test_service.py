import copy
import hashlib
import json
import shutil
import subprocess
import urllib.error
from pathlib import Path

import pytest

from draft.llm import LLMConfigError
from mdfixer import service_runner
from mdfixer.api import _llm_from_headers
from mdfixer.job_store import IdempotencyConflict, JobStore
from mdfixer.service_runner import run_contract_repair, validate_request

REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "fixtures" / "e10"


def request() -> dict:
    return {
        "schema_version": "1.0",
        "trace_id": "trace-repair-test",
        "job_type": "REPAIR",
        "input": {
            "repository": {"url": "https://example.com/repo.git", "commit": "a" * 40},
            "commit": "a" * 40,
            "md_report_uri": "artifact://pair07/test/report.json",
            "md_report_sha256": "a" * 64,
            "makefile": "Makefile",
            "env": {
                "build": "make app",
                "verify": "./app",
                "verify_expected_output": "1",
            },
            "recheck": {
                "header": "config.h",
                "macro": "VALUE",
                "old_value": "1",
                "new_value": "2",
                "target": "app",
                "program": "./app",
                "expected_output": "2",
                "timeout_seconds": 120,
            },
            "idempotency_key": "repair-test-1",
        },
    }


def test_validate_request_accepts_contract_shape():
    data = validate_request(request())
    assert data["env"]["build"] == "make app"
    assert data["recheck"]["macro"] == "VALUE"
    assert data["commit"] == "a" * 40


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(schema_version="2.0"),
        lambda p: p.update(job_type="DRAFT"),
        lambda p: p.update(trace_id="bad"),
        lambda p: p["input"].update(commit="b" * 40),
        lambda p: p["input"]["repository"].update(url="file:///tmp/repo"),
        lambda p: p["input"].update(md_report_uri="fixed-md.json"),
        lambda p: p["input"].update(makefile=""),
        lambda p: p["input"]["env"].update(verify=""),
        lambda p: p["input"].update(idempotency_key=""),
        lambda p: p["input"].update(unknown=True),
        lambda p: p["input"].pop("recheck"),
        lambda p: p["input"].pop("md_report_sha256"),
        lambda p: p["input"]["recheck"].update(timeout_seconds=0),
    ],
)
def test_validate_request_rejects_invalid(mutate):
    payload = request()
    mutate(payload)
    with pytest.raises(ValueError):
        validate_request(payload)


def test_llm_headers_optional():
    assert _llm_from_headers({}) is None
    config = _llm_from_headers({
        "X-LLM-Api-Url": "https://example.com/v1",
        "X-LLM-Api-Key": "fake-key",
        "X-LLM-Model": "fake-model",
    })
    assert config.model == "fake-model"
    with pytest.raises(LLMConfigError):
        _llm_from_headers({"X-LLM-Api-Url": "https://example.com/v1"})


def test_job_store_idempotency_and_recovery(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.db")
    data = validate_request(request())
    job = store.create(
        "trace-repair-test",
        data,
        "repair-test-1",
        tmp_path / "jobs" / "job-1",
        job_id="job-1",
    )
    same = store.create(
        "trace-repair-test",
        data,
        "repair-test-1",
        tmp_path / "jobs" / "job-2",
        job_id="job-2",
    )
    assert job["job_id"] == same["job_id"] == "job-1"

    changed = copy.deepcopy(data)
    changed["env"]["build"] = "cmake"
    with pytest.raises(IdempotencyConflict):
        store.create(
            "trace-repair-test",
            changed,
            "repair-test-1",
            tmp_path / "jobs" / "job-3",
            job_id="job-3",
        )

    store.mark_running("job-1")
    assert store.recover_interrupted() == 1
    recovered = store.get("job-1")
    assert recovered["status"] == "FAILED"
    assert recovered["error"]["code"] == "REPAIR_6000"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_contract_runner_accepts_fixture(tmp_path: Path, monkeypatch):
    if not shutil.which("make") or not shutil.which("gcc"):
        pytest.skip("GNU Make or gcc is unavailable")
    project = tmp_path / "project"
    project.mkdir()
    for name in ("main.c", "config.h", "unused.h"):
        shutil.copyfile(FIXTURE / name, project / name)
    (project / "Makefile").write_bytes((FIXTURE / "Makefile").read_bytes())
    _git(project, "init", "-q")
    _git(project, "add", ".")
    _git(project, "-c", "user.name=Repair Test", "-c", "user.email=repair@example.invalid", "commit", "-qm", "input")
    commit = _git(project, "rev-parse", "HEAD")
    text = (project / "Makefile").read_text(encoding="utf-8")
    line = next(index for index, value in enumerate(text.splitlines(), 1) if value.startswith("main.o:"))
    report = {
        "schema_version": "1.0",
        "repository": {"url": "https://example.com/repo.git", "commit": commit},
        "configuration_id": "repair-test",
        "producer_job_id": "job-repair-test",
        "findings": [
            {
                "finding_id": "MISSING-main-o-config-h",
                "type": "MISSING",
                "target": "main.o",
                "dependency": "config.h",
                "commit": commit,
                "position": {
                    "file": "Makefile",
                    "line": line,
                    "declaration": text.splitlines()[line - 1].strip(),
                },
                "detector": "INSTRUCTOR_ORACLE",
            },
            {
                "finding_id": "REDUNDANT-main-o-unused-h",
                "type": "REDUNDANT",
                "target": "main.o",
                "dependency": "unused.h",
                "commit": commit,
                "position": {
                    "file": "Makefile",
                    "line": line,
                    "declaration": text.splitlines()[line - 1].strip(),
                },
                "detector": "INSTRUCTOR_ORACLE",
            },
        ],
    }
    artifact = tmp_path / "data" / "artifacts" / "pair07" / "test" / "report.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps(report), encoding="utf-8")

    payload = request()
    payload["input"]["repository"]["commit"] = commit
    payload["input"]["commit"] = commit
    payload["input"]["md_report_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    data = validate_request(payload)

    def fake_clone(repository, destination, deadline):
        shutil.copytree(project, destination)

    monkeypatch.setattr(service_runner, "_clone", fake_clone)
    output = run_contract_repair(data, None, "job-test", tmp_path / "work", tmp_path / "data")
    assert "config.h" in output["git_patch"]
    assert output["recheck"]["build"]["ok"] is True
    assert output["recheck"]["test"]["ok"] is True
    assert output["recheck"]["detection"]["ok"] is True
    assert output["accepted"] is True
    assert output["validation"] == {
        "patch_applies": True,
        "build": True,
        "test": True,
        "header_change": True,
        "no_missing": True,
        "redundant_preserved": True,
    }
    assert output["recheck"]["incremental"]["ok"] is True
    assert output["rejected_candidates"] == []


def test_validate_request_rejects_bad_recheck_and_hash():
    payload = request()
    payload["input"]["recheck"].pop("macro")
    with pytest.raises(ValueError):
        validate_request(payload)

    payload = request()
    payload["input"]["md_report_sha256"] = "not-a-sha"
    with pytest.raises(ValueError):
        validate_request(payload)

    payload = request()
    payload["input"]["time_limit"] = 0
    with pytest.raises(ValueError):
        validate_request(payload)


def test_report_hash_is_checked(tmp_path: Path):
    report = {
        "schema_version": "1.0",
        "repository": {"url": "https://example.com/repo.git", "commit": "a" * 40},
        "configuration_id": "test",
        "findings": [],
    }
    artifact = tmp_path / "artifacts" / "pair07" / "test" / "report.json"
    artifact.parent.mkdir(parents=True)
    raw = json.dumps(report).encode("utf-8")
    artifact.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()

    loaded = service_runner._resolve_report(
        tmp_path,
        "artifact://pair07/test/report.json",
        digest,
        None,
    )
    assert loaded["configuration_id"] == "test"

    with pytest.raises(service_runner.ServiceError) as exc:
        service_runner._resolve_report(
            tmp_path,
            "artifact://pair07/test/report.json",
            "0" * 64,
            None,
        )
    assert exc.value.code == "REPAIR_6004"


def test_artifact_path_limits_pair(tmp_path: Path):
    from mdfixer.api import MdfixerService

    service = MdfixerService(tmp_path)
    artifact = tmp_path / "artifacts" / "pair07" / "job-1" / "patch.diff"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("diff", encoding="utf-8")
    assert service.artifact_path("pair07", "job-1", "patch.diff") == artifact
    with pytest.raises(PermissionError):
        service.artifact_path("other", "job-1", "patch.diff")
    with pytest.raises(FileNotFoundError):
        service.artifact_path("pair07", "job-1", "missing.diff")
    service.stop()


def test_artifact_endpoint_serves_file(tmp_path: Path):
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer

    from mdfixer.api import MdfixerService, _handler

    service = MdfixerService(tmp_path)
    artifact = tmp_path / "artifacts" / "pair07" / "job-1" / "patch.diff"
    artifact.parent.mkdir(parents=True)
    raw = b"diff --git a/Makefile b/Makefile\n"
    artifact.write_bytes(raw)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1/artifacts/pair07/job-1/patch.diff"
        with urllib.request.urlopen(url, timeout=5) as response:
            assert response.status == 200
            assert response.read() == raw
            assert response.headers["X-Artifact-Sha256"] == hashlib.sha256(raw).hexdigest()
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/v1/artifacts/other/job-1/patch.diff",
                timeout=5,
            )
        assert exc.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        service.stop()


def test_report_type_is_checked(tmp_path: Path):
    report = tmp_path / "artifacts" / "pair07" / "test" / "report.txt"
    report.parent.mkdir(parents=True)
    report.write_text("{}", encoding="utf-8")
    with pytest.raises(service_runner.ServiceError) as exc:
        service_runner._resolve_report(
            tmp_path,
            "artifact://pair07/test/report.txt",
            "0" * 64,
            None,
        )
    assert exc.value.code == "REPAIR_6003"
