import json

import pytest

from draft.docker import CommandResult
from draft.pipeline import run_repair
from fake_docker import BUILD_OK, IMAGE_ID, MISSING_MAKE, RUN_OK, FakeDocker
from fake_llm import FakeLLM, fenced_dockerfile


def reply(fixture_dir, marker=""):
    text = (fixture_dir / "Dockerfile.reference").read_text(encoding="utf-8")
    if marker:
        text = text.replace("RUN make\n", f"RUN echo {marker}\nRUN make\n", 1)
    return fenced_dockerfile(text)


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
    assert "echo first" in llm.prompts[1]
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


def test_invalid_candidate_is_rejected_and_next_round_can_recover(tmp_path, fixture_dir):
    baseline = (fixture_dir / "Dockerfile.broken").read_text(encoding="utf-8")
    invalid = baseline.replace('CMD ["./hello"]', 'CMD ["echo", "hello E3"]')
    llm = FakeLLM([fenced_dockerfile(invalid), reply(fixture_dir)])
    runner = FakeDocker([MISSING_MAKE, BUILD_OK], [RUN_OK])
    outcome = run_repair(fixture_dir, out_dir=tmp_path / "run", llm=llm, runner=runner)
    assert outcome.ok() and outcome.rounds[0].build["exit_code"] is None
    assert "CMD" in outcome.rounds[0].rejected[0]["reason"]
    assert len(runner.dockerfiles) == 2
