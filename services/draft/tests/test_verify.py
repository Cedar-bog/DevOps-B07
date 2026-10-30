import pytest

from draft.docker import CommandResult, DockerError
from draft.verify import outputs_match, run_image
from fake_docker import IMAGE_ID, RUN_OK, FakeDocker


@pytest.mark.parametrize("actual,expected,match", [
    ("hello E3\r\n\n", "hello E3", True),
    ("hello E3   \n \n", "hello E3", True),
    ("hello E3\nextra\n", "hello E3", False),
    ("prefix hello E3", "hello E3", False),
    ("\nhello E3", "hello E3", False),
])
def test_exact_normalized_output(actual, expected, match):
    assert outputs_match(actual, expected) is match


def test_verification_resource_limits_and_cleanup(tmp_path):
    runner = FakeDocker([], [RUN_OK])
    result = run_image(IMAGE_ID, expected_output="hello E3", container_name="draft-test", cidfile=tmp_path / "cid", runner=runner)
    assert result.ok() and result.container_removed and result.container_id == "fake-container-id"
    command = result.command
    for flag, value in (("--network", "none"), ("--cpus", "2"), ("--memory", "1g"), ("--pids-limit", "256")):
        assert command[command.index(flag) + 1] == value
    assert "--rm" not in command
    assert runner.commands[-1] == ["docker", "rm", "-f", "draft-test"]


def test_timeout_cleanup_reports_actual_failure(tmp_path):
    runner = FakeDocker([], [CommandResult(-1, "partial", "", 1, True)], cleanup_ok=False)
    result = run_image(IMAGE_ID, expected_output="hello E3", container_name="timeout", runner=runner)
    assert result.timed_out and not result.ok() and not result.container_removed
    assert sum(command[1] == "rm" for command in runner.commands) == 1


def test_stderr_cannot_satisfy_expected_output():
    runner = FakeDocker([], [CommandResult(0, "wrong", "hello E3", 1)])
    result = run_image(IMAGE_ID, expected_output="hello E3", container_name="wrong", runner=runner)
    assert result.exit_code == 0 and not result.output_matches and not result.ok()


def test_invalid_image_rejected_before_run():
    with pytest.raises(DockerError):
        run_image("tag-only", expected_output="hello E3", container_name="invalid")


def test_cleanup_on_runner_exception():
    commands = []

    def runner(command, cwd, timeout):
        commands.append(command)
        if command[1] == "run":
            raise FileNotFoundError("docker")
        return CommandResult(0, "", "", 0)

    with pytest.raises(DockerError):
        run_image(IMAGE_ID, expected_output="hello E3", container_name="missing", runner=runner)
    assert commands[-1][1:3] == ["rm", "-f"]
