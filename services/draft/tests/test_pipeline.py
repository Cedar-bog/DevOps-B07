import json

import pytest

from draft.docker import CommandResult
from draft.llm import LLMClient, LLMConfig, LLMRequestError
from draft.pipeline import SavedCandidates, run_repair
from fake_docker import BUILD_OK, IMAGE_ID, MISSING_MAKE, RUN_OK, FakeDocker
from fake_llm import FakeLLM, fenced_dockerfile


def reply(fixture_dir, marker=""):
    text = (fixture_dir / "Dockerfile.reference").read_text(encoding="utf-8")
    return fenced_dockerfile(text + (f"\n# {marker}\n" if marker else ""))


def payload(outcome):
    return json.loads(outcome.result_path.read_text(encoding="utf-8"))


def test_repair_failure_then_success_keeps_round_evidence(tmp_path, fixture_dir):
    llm = FakeLLM([reply(fixture_dir, "first"), reply(fixture_dir, "second")])
    runner = FakeDocker([MISSING_MAKE, MISSING_MAKE, BUILD_OK], [RUN_OK])
    original = (fixture_dir / "Dockerfile.broken").read_bytes()
    result = run_repair(fixture_dir, out_dir=tmp_path / "run", llm=llm.client(), runner=runner)
    assert result.ok() and result.status == "SUCCEEDED" and result.error_code is None
    assert len(result.rounds) == 2 and result.rounds[0].verify is None
    assert result.image_id == IMAGE_ID and result.verify.ok()
    assert "make: not found" in llm.prompts[0] and "Tiny Greeting" in llm.prompts[0]
    assert "# first" in llm.prompts[1]
    saved = payload(result)
    assert saved["initial"]["build"]["failure_class"] == "MISSING_MAKE"
    assert saved["final"]["verify"]["container_removed"]
    assert saved["final"]["verify"]["container_id"] == "fake-container-id"
    assert saved["error"] is None and saved["input"]["model"] == "fake-llm"
    assert (fixture_dir / "Dockerfile.broken").read_bytes() == original
    for record in saved["rounds"]:
        for relative in record["evidence"].values():
            assert (result.paths.root / relative).is_file()
    assert (result.paths.iter_dir(1) / "reply.txt").read_bytes() == llm.served[0].text.encode("utf-8")
    assert (result.paths.iter_dir(1) / "build-exit-code.txt").read_text().strip() == "1"
    assert not (result.paths.iter_dir(1) / "verify.log").exists()
    assert (result.paths.iter_dir(2) / "verify.log").read_text() == "hello E3\n"


def test_first_candidate_can_succeed(tmp_path, fixture_dir):
    runner = FakeDocker([MISSING_MAKE, BUILD_OK], [RUN_OK])
    outcome = run_repair(fixture_dir, out_dir=tmp_path / "run", llm=FakeLLM([reply(fixture_dir)]), runner=runner)
    assert outcome.ok() and len(outcome.rounds) == 1


def test_candidate_build_failure_never_runs_verification(tmp_path, fixture_dir):
    runner = FakeDocker([MISSING_MAKE, MISSING_MAKE])
    outcome = run_repair(fixture_dir, out_dir=tmp_path / "run", llm=FakeLLM([reply(fixture_dir)]), runner=runner, max_iterations=1)
    assert outcome.status == "FAILED" and outcome.error_code == "ENV_3002"
    assert outcome.rounds[0].verify is None
    assert not any(command[1] == "run" for command in runner.commands)


def test_output_mismatch_continues_then_succeeds(tmp_path, fixture_dir):
    wrong = CommandResult(0, "hello E3\nextra\n", "", 1)
    runner = FakeDocker([MISSING_MAKE, BUILD_OK, BUILD_OK], [wrong, RUN_OK])
    llm = FakeLLM([reply(fixture_dir, "one"), reply(fixture_dir, "two")])
    outcome = run_repair(fixture_dir, out_dir=tmp_path / "run", llm=llm, runner=runner)
    assert outcome.ok() and not outcome.rounds[0].verify["output_matches"]
    assert "extra" in llm.prompts[1] and "Verification failed" in llm.prompts[1]


@pytest.mark.parametrize("run_result", [CommandResult(0, "wrong", "", 1), CommandResult(7, "hello E3\n", "failed", 1)])
def test_verification_failure_has_no_build_error_code(tmp_path, fixture_dir, run_result):
    runner = FakeDocker([MISSING_MAKE, BUILD_OK], [run_result])
    outcome = run_repair(fixture_dir, out_dir=tmp_path / "run", llm=FakeLLM([reply(fixture_dir)]), runner=runner, max_iterations=1)
    assert outcome.status == "FAILED" and outcome.error_code is None
    assert payload(outcome)["error"]["code"] is None


def test_run_timeout_stops_and_cleans_container(tmp_path, fixture_dir):
    runner = FakeDocker([MISSING_MAKE, BUILD_OK], [CommandResult(-1, "partial", "timeout", 1, True)])
    llm = FakeLLM([reply(fixture_dir)])
    outcome = run_repair(fixture_dir, out_dir=tmp_path / "run", llm=llm, runner=runner)
    assert outcome.status == "TIMED_OUT" and outcome.error_code == "EXEC_4002"
    assert outcome.verify.container_removed and outcome.verify.container_id
    assert len(llm.calls) == 1
    assert runner.commands[-1][1:3] == ["rm", "-f"]


@pytest.mark.parametrize("initial_timeout", [True, False])
def test_build_timeout_is_terminal(tmp_path, fixture_dir, initial_timeout):
    timeout = CommandResult(-1, "partial build", "timeout", 1, True)
    runner = FakeDocker([timeout] if initial_timeout else [MISSING_MAKE, timeout])
    outcome = run_repair(fixture_dir, out_dir=tmp_path / "run", llm=FakeLLM([reply(fixture_dir)]), runner=runner)
    assert outcome.status == "TIMED_OUT" and outcome.error_code == "EXEC_4002"
    assert not any(command[1] == "run" for command in runner.commands)


def test_invalid_candidate_is_rejected_and_next_round_can_recover(tmp_path, fixture_dir):
    baseline = (fixture_dir / "Dockerfile.broken").read_text(encoding="utf-8")
    invalid = baseline.replace('CMD ["./hello"]', 'CMD ["echo", "hello E3"]')
    llm = FakeLLM([fenced_dockerfile(invalid), reply(fixture_dir)])
    runner = FakeDocker([MISSING_MAKE, BUILD_OK], [RUN_OK])
    outcome = run_repair(fixture_dir, out_dir=tmp_path / "run", llm=llm, runner=runner)
    assert outcome.ok() and outcome.rounds[0].build["exit_code"] is None
    assert "CMD" in outcome.rounds[0].rejected[0]["reason"]
    assert len(runner.dockerfiles) == 2


def test_zero_candidates_are_bounded_and_keep_raw_replies(tmp_path, fixture_dir):
    llm = FakeLLM(["no Dockerfile"], repeat_last=True)
    runner = FakeDocker([MISSING_MAKE])
    outcome = run_repair(fixture_dir, out_dir=tmp_path / "run", llm=llm, runner=runner, max_iterations=2)
    assert not outcome.ok() and outcome.error_code == "ENV_3002" and len(outcome.rounds) == 2
    assert all(record.build["exit_code"] is None for record in outcome.rounds)
    assert (outcome.paths.iter_dir(2) / "reply.txt").read_text() == "no Dockerfile"
    assert len(runner.commands) == 1


def test_used_candidate_is_not_rebuilt(tmp_path, fixture_dir):
    llm = FakeLLM([reply(fixture_dir)], repeat_last=True)
    runner = FakeDocker([MISSING_MAKE, MISSING_MAKE])
    outcome = run_repair(fixture_dir, out_dir=tmp_path / "run", llm=llm, runner=runner, max_iterations=2)
    assert not outcome.ok() and len(runner.dockerfiles) == 2
    assert outcome.rounds[1].build["exit_code"] is None and "R1" in outcome.rounds[1].build["reason"]


def test_llm_error_is_terminal_and_saved(tmp_path, fixture_dir):
    llm = FakeLLM([LLMRequestError("model unavailable")])
    outcome = run_repair(fixture_dir, out_dir=tmp_path / "run", llm=llm, runner=FakeDocker([MISSING_MAKE]))
    assert outcome.status == "FAILED" and "model unavailable" in outcome.message
    assert len(llm.calls) == 1 and len(payload(outcome)["rounds"]) == 1


def test_missing_docker_is_recorded(tmp_path, fixture_dir):
    def missing(*args):
        raise FileNotFoundError("docker not installed")

    outcome = run_repair(fixture_dir, out_dir=tmp_path / "run", llm=FakeLLM(), runner=missing)
    assert outcome.status == "FAILED" and "docker not installed" in outcome.message
    assert outcome.result_path.is_file()


def test_initial_success_avoids_llm(tmp_path, fixture_dir):
    llm = FakeLLM()
    outcome = run_repair(fixture_dir, out_dir=tmp_path / "run", llm=llm, runner=FakeDocker([BUILD_OK], [RUN_OK]))
    assert outcome.ok() and not outcome.rounds and not llm.calls
    assert payload(outcome)["initial"]["verify"]["output_matches"]


def test_saved_candidates_skip_model_configuration(tmp_path, fixture_dir):
    saved = fixture_dir / "Dockerfile.reference"
    outcome = run_repair(fixture_dir, out_dir=tmp_path / "run", llm=SavedCandidates([saved]), runner=FakeDocker([MISSING_MAKE, BUILD_OK], [RUN_OK]))
    assert outcome.ok() and payload(outcome)["input"]["prompt_mode"] == "skipped"


def test_result_is_checkpointed_before_next_llm_call(tmp_path, fixture_dir):
    out = tmp_path / "run"

    def second(messages):
        checkpoint = json.loads((out / "result.json").read_text(encoding="utf-8"))
        assert len(checkpoint["rounds"]) == 1
        assert checkpoint["rounds"][0]["build"]["exit_code"] == 1
        return reply(fixture_dir, "second")

    llm = FakeLLM([reply(fixture_dir, "first"), second])
    outcome = run_repair(fixture_dir, out_dir=out, llm=llm, runner=FakeDocker([MISSING_MAKE, MISSING_MAKE, BUILD_OK], [RUN_OK]))
    assert outcome.ok()


def test_endpoint_query_and_config_key_are_not_saved(tmp_path, fixture_dir):
    fake = FakeLLM([reply(fixture_dir)])
    secret = "test-value"
    config = LLMConfig(api_url="https://example.invalid/v1?credential=" + secret, api_key=secret, model="test")
    llm = LLMClient(config, transport=fake.transport)
    outcome = run_repair(fixture_dir, out_dir=tmp_path / "run", llm=llm, runner=FakeDocker([MISSING_MAKE, BUILD_OK], [RUN_OK]))
    assert outcome.ok()
    for file in outcome.paths.root.rglob("*"):
        if file.is_file():
            assert secret not in file.read_text(encoding="utf-8")
    assert "?" not in payload(outcome)["input"]["endpoint"]


@pytest.mark.parametrize("options", [{"max_iterations": 0}, {"build_timeout": -1}, {"verify_timeout": float("nan")}, {"prompt_mode": "invalid"}])
def test_bad_inputs_are_rejected_before_creating_output(tmp_path, fixture_dir, options):
    with pytest.raises(ValueError):
        run_repair(fixture_dir, out_dir=tmp_path / "run", llm=FakeLLM(), **options)
    assert not (tmp_path / "run").exists()
