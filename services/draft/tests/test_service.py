import copy
from pathlib import Path

import pytest

from draft.api import _llm_from_headers
from draft.candidate import basic_reasons
from draft.job_store import IdempotencyConflict, JobStore
from draft.llm import LLMConfigError
from draft.service_runner import ValidationError, validate_request


def request() -> dict:
    return {
        "schema_version": "1.0",
        "trace_id": "trace-service-test",
        "job_type": "DRAFT",
        "input": {
            "repository": {
                "url": "https://example.com/project.git",
                "commit": "a" * 40,
            },
            "build": {"command": "make", "workdir": "/app"},
            "verify": {"command": "make test"},
            "max_iterations": 3,
            "time_limit": 60,
            "idempotency_key": "service-test-1",
        },
    }


def test_validate_request_accepts_contract_shape():
    data = validate_request(request())
    assert data["build"]["workdir"] == "/app"
    assert data["repository"]["commit"] == "a" * 40


def test_validate_request_defaults_workdir():
    payload = request()
    del payload["input"]["build"]["workdir"]
    data = validate_request(payload)
    assert data["build"]["workdir"] == "/app"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(schema_version="2.0"),
        lambda p: p.update(job_type="REPAIR"),
        lambda p: p.update(trace_id="bad"),
        lambda p: p["input"]["repository"].update(url="file:///tmp/repo"),
        lambda p: p["input"]["repository"].update(commit="a" * 39),
        lambda p: p["input"]["build"].update(command=""),
        lambda p: p["input"]["verify"].update(command=""),
        lambda p: p["input"].update(max_iterations=0),
        lambda p: p["input"].update(time_limit=0),
        lambda p: p["input"].update(idempotency_key=""),
        lambda p: p["input"].update(unknown=True),
    ],
)
def test_validate_request_rejects_invalid(mutate):
    payload = request()
    mutate(payload)
    with pytest.raises(ValidationError):
        validate_request(payload)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["input"]["build"].update(command=" "),
        lambda p: p["input"]["build"].update(workdir=""),
        lambda p: p["input"]["build"].update(workdir=" "),
        lambda p: p["input"]["verify"].update(command=" "),
    ],
)
def test_validate_request_rejects_blank_internal_fields(mutate):
    payload = request()
    mutate(payload)
    with pytest.raises(ValidationError):
        validate_request(payload)


def test_basic_candidate_reasons():
    assert basic_reasons("FROM ubuntu:22.04\nWORKDIR /app\nCOPY . .\nRUN make\n") == []
    assert basic_reasons("RUN make\n")
    assert basic_reasons("FROM a\nFROM b\n")
    assert basic_reasons("FROM ubuntu:22.04\nRUN rm -rf .\n")


def test_llm_headers():
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
        "trace-service-test",
        data,
        "service-test-1",
        tmp_path / "jobs" / "job-1",
        job_id="job-1",
    )
    same = store.create(
        "trace-service-test",
        data,
        "service-test-1",
        tmp_path / "jobs" / "job-2",
        job_id="job-2",
    )
    assert job["job_id"] == same["job_id"] == "job-1"

    changed = copy.deepcopy(data)
    changed["build"]["command"] = "cmake"
    with pytest.raises(IdempotencyConflict):
        store.create(
            "trace-service-test",
            changed,
            "service-test-1",
            tmp_path / "jobs" / "job-3",
            job_id="job-3",
        )

    store.mark_running("job-1")
    assert store.recover_interrupted() == 1
    recovered = store.get("job-1")
    assert recovered["status"] == "FAILED"
    assert recovered["error"]["code"] == "ANALYSIS_5001"


def test_job_store_cleanup(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.db")
    data = validate_request(request())
    store.create(
        "trace-service-test",
        data,
        "service-test-1",
        tmp_path / "jobs" / "job-1",
        job_id="job-1",
    )
    store.mark_running("job-1")
    store.succeed(
        "job-1",
        {
            "dockerfile": "FROM ubuntu:22.04\n",
            "image_ref": "b07-draft:test",
            "rounds": [],
            "final_build": "success",
            "final_verify": "ok",
        },
    )
    removed = store.cleanup("9999-12-31T23:59:59Z")
    assert removed[0]["job_id"] == "job-1"
    assert store.get("job-1") is None


def test_validate_request_accepts_expected_output():
    payload = request()
    payload["input"]["verify"]["expected_output"] = "1"
    data = validate_request(payload)
    assert data["verify"]["expected_output"] == "1"


def test_validate_request_rejects_non_string_expected_output():
    payload = request()
    payload["input"]["verify"]["expected_output"] = 1
    with pytest.raises(ValidationError):
        validate_request(payload)


def test_round_log_is_written(tmp_path: Path):
    from draft.service_runner import _write_round_log

    relative = _write_round_log(tmp_path, 1, "build", "full log\n")
    assert relative == "logs/round-01-build.log"
    assert (tmp_path / relative).read_text(encoding="utf-8") == "full log\n"
