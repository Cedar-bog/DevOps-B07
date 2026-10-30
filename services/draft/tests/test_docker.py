import subprocess
import sys

import pytest

from draft.docker import (
    CommandResult, DockerError, build_image, classify_failure, default_runner,
    locate_failed_step, remove_image,
)
from fake_docker import BUILD_OK, IMAGE_ID, MISSING_MAKE, FakeDocker


def test_build_records_command_and_image(fixture_dir):
    runner = FakeDocker([BUILD_OK])
    result = build_image(fixture_dir, dockerfile="Dockerfile.reference", tag="draft:test", no_cache=True, runner=runner)
    assert result.ok() and result.image_id == IMAGE_ID
    assert result.command == ["docker", "build", "--progress=plain", "-f", "Dockerfile.reference", "-t", "draft:test", ".", "--no-cache"]
    assert result.failure_class == "NONE"
    assert result.started_at.endswith("Z") and result.finished_at.endswith("Z")


def test_build_failure_and_clean_log(fixture_dir):
    runner = FakeDocker([CommandResult(1, "\x1b[31m#8 [4/4] RUN make\r\n", "make: not found\x1b[0m", 1)])
    result = build_image(fixture_dir, dockerfile="Dockerfile.broken", runner=runner)
    assert not result.ok() and result.image_id is None
    assert result.failed_step == "RUN make"
    assert result.failure_class == "MISSING_MAKE"
    assert "\x1b" not in result.log and "\r" not in result.log
    assert len(runner.commands) == 1


@pytest.mark.parametrize("message,code,timed_out,expected", [
    ("make: not found", 1, False, "MISSING_MAKE"),
    ("make: command not found", 1, False, "MISSING_MAKE"),
    ("Unable to locate package xyz", 100, False, "APT_ERROR"),
    ("E: repository has no Release file", 1, False, "APT_ERROR"),
    ("Temporary failure resolving host", 1, False, "NETWORK"),
    ("TLS handshake timeout", 1, False, "NETWORK"),
    ("unknown", 1, False, "OTHER"),
    ("make: not found", 0, False, "NONE"),
    ("make: not found", -1, True, "TIMEOUT"),
])
def test_failure_classification(message, code, timed_out, expected):
    assert classify_failure(message, code, timed_out) == expected


def test_build_timeout_does_not_inspect(fixture_dir):
    runner = FakeDocker([CommandResult(-1, "partial\n", "timeout", 2, True)])
    result = build_image(fixture_dir, dockerfile="Dockerfile.broken", runner=runner)
    assert result.timed_out and result.failure_class == "TIMEOUT"
    assert len(runner.commands) == 1


def test_inspection_failure_is_not_build_success(fixture_dir):
    result = build_image(fixture_dir, dockerfile="Dockerfile.reference", runner=FakeDocker([BUILD_OK], inspect_ok=False))
    assert result.exit_code == 0 and not result.ok() and result.image_id is None


def test_missing_input_and_executable(fixture_dir, tmp_path):
    with pytest.raises(DockerError):
        build_image(tmp_path / "missing")

    def missing(*args):
        raise FileNotFoundError("missing executable")

    with pytest.raises(DockerError):
        build_image(fixture_dir, dockerfile="Dockerfile.broken", runner=missing)
    assert remove_image("test", runner=missing) is False


def test_locates_last_buildkit_step():
    assert locate_failed_step("#1 [stage 1/2] FROM base\n#2 [stage 2/2] RUN make\n#2 ERROR: failed") == "RUN make"
    assert locate_failed_step("daemon unavailable") is None


def test_default_runner_keeps_streams_separate(tmp_path):
    result = default_runner([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)"], tmp_path, 10)
    assert result.exit_code == 7 and result.stdout.strip() == "out" and result.stderr.strip() == "err"


def test_default_runner_preserves_partial_timeout_output(monkeypatch, tmp_path):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1, output=b"partial\n", stderr=b"detail\n")

    monkeypatch.setattr(subprocess, "run", timeout)
    result = default_runner(["docker", "build"], tmp_path, 1)
    assert result.exit_code == -1 and result.timed_out
    assert result.stdout == "partial\n" and "detail" in result.stderr
