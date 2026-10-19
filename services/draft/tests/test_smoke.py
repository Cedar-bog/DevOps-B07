"""Unit tests for the DRAFT smoke check."""

from __future__ import annotations

import json

import pytest

from draft.smoke import (
    DOCKERFILE_NAME,
    REQUIRED_FILES,
    SmokeError,
    SmokeResult,
    run_smoke,
    write_result,
)


def _make_fixture(tmp_path, *, files=REQUIRED_FILES):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    for name in files:
        (fixture / name).write_text("placeholder", encoding="utf-8")
    return fixture


def _make_runner(exit_code, output):
    calls: list[tuple] = []

    def run(command, cwd, timeout):
        calls.append((command, cwd, timeout))
        return exit_code, output

    return run, calls


def test_run_smoke_ok_when_build_fails_with_signature(tmp_path):
    fixture = _make_fixture(tmp_path)
    runner, calls = _make_runner(1, "gcc: error ... make: not found")
    result = run_smoke(fixture, runner=runner)

    assert result.ok is True
    assert result.exit_code == 1
    assert result.signature_found is True
    assert result.dockerfile == DOCKERFILE_NAME
    # first invocation is the build, second is the cleanup rmi
    assert calls[0][0][:2] == ["docker", "build"]
    assert calls[1][0][:2] == ["docker", "rmi"]


def test_run_smoke_not_ok_when_build_succeeds(tmp_path):
    fixture = _make_fixture(tmp_path)
    runner, _ = _make_runner(0, "Successfully built abc123")
    result = run_smoke(fixture, runner=runner)

    assert result.ok is False
    assert result.signature_found is False


def test_run_smoke_missing_fixture_dir(tmp_path):
    with pytest.raises(SmokeError):
        run_smoke(tmp_path / "does-not-exist")


def test_run_smoke_missing_required_file(tmp_path):
    fixture = _make_fixture(tmp_path, files=("main.c",))
    with pytest.raises(SmokeError):
        run_smoke(fixture)


def test_run_smoke_executable_not_found(tmp_path):
    fixture = _make_fixture(tmp_path)

    def runner(command, cwd, timeout):
        raise FileNotFoundError("docker")

    with pytest.raises(SmokeError):
        run_smoke(fixture, runner=runner)


def test_result_to_payload():
    result = SmokeResult(
        run_id="r1",
        dockerfile="Dockerfile.broken",
        image_tag="tag",
        exit_code=1,
        signature="make: not found",
        signature_found=True,
        ok=True,
        log_tail="tail",
        started_at="s",
        finished_at="f",
        duration_s=0.5,
    )
    payload = result.to_payload()
    assert payload["schema_version"] == "1.0"
    assert payload["service"] == "draft"
    assert payload["ok"] is True
    assert payload["observed"] == {"exit_code": 1, "log_has_signature": True}


def test_write_result(tmp_path):
    result = SmokeResult(
        run_id="r1",
        dockerfile="Dockerfile.broken",
        image_tag="tag",
        exit_code=1,
        signature="make: not found",
        signature_found=True,
        ok=True,
        log_tail="tail",
        started_at="s",
        finished_at="f",
        duration_s=0.5,
    )
    target = write_result(result, tmp_path / "sub" / "smoke.json")
    assert target.exists()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["ok"] is True
