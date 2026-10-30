"""Unit tests for the DRAFT command line entry point."""

from __future__ import annotations

import json

import pytest

from draft import __version__
from draft.cli import SERVICE, build_parser, main
from draft.smoke import SmokeError, SmokeResult
from fake_docker import BUILD_OK, MISSING_MAKE, RUN_OK, FakeDocker


def _make_result(*, ok: bool = True, exit_code: int = 1, signature_found: bool = True) -> SmokeResult:
    return SmokeResult(
        run_id="20240101T000000Z",
        dockerfile="Dockerfile.broken",
        image_tag="e4-draft-smoke:test",
        exit_code=exit_code,
        signature="make: not found",
        signature_found=signature_found,
        ok=ok,
        log_tail="gcc: make: not found",
        started_at="2024-01-01T00:00:00Z",
        finished_at="2024-01-01T00:00:01Z",
        duration_s=1.0,
    )


def test_build_parser_exposes_version_and_smoke():
    parser = build_parser()
    assert parser.parse_args(["version"]).command == "version"
    assert parser.parse_args(["smoke"]).command == "smoke"


def test_main_version(capsys):
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == f"{SERVICE} {__version__}"


def test_main_no_command_returns_2():
    assert main([]) == 2


def test_main_smoke_ok_prints_json(monkeypatch, capsys):
    monkeypatch.setattr("draft.cli.run_smoke", lambda *a, **kw: _make_result(ok=True))
    assert main(["smoke"]) == 0
    assert '"ok": true' in capsys.readouterr().out


def test_main_smoke_not_ok_returns_1(monkeypatch):
    monkeypatch.setattr(
        "draft.cli.run_smoke",
        lambda *a, **kw: _make_result(ok=False, exit_code=0, signature_found=False),
    )
    assert main(["smoke"]) == 1


def test_main_smoke_error_returns_1(monkeypatch, capsys):
    def boom(*a, **kw):
        raise SmokeError("fixture directory not found")

    monkeypatch.setattr("draft.cli.run_smoke", boom)
    assert main(["smoke"]) == 1
    assert "smoke error" in capsys.readouterr().err


def test_main_smoke_writes_output_file(monkeypatch, tmp_path):
    monkeypatch.setattr("draft.cli.run_smoke", lambda *a, **kw: _make_result(ok=True))
    target = tmp_path / "smoke.json"
    assert main(["smoke", "--output", str(target)]) == 0
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["observed"]["exit_code"] == 1


def _install_runner(monkeypatch, runner):
    monkeypatch.setattr("draft.docker.default_runner", runner)
    monkeypatch.setattr("draft.verify.default_runner", runner)


def test_repair_saved_candidate_cli_json(monkeypatch, tmp_path, fixture_dir, capsys):
    _install_runner(monkeypatch, FakeDocker([MISSING_MAKE, BUILD_OK], [RUN_OK]))
    for name in ("LLM_API_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    code = main([
        "repair", "--fixture-dir", str(fixture_dir), "--out", str(tmp_path / "out"),
        "--candidate", str(fixture_dir / "Dockerfile.reference"), "--json",
    ])
    captured = capsys.readouterr()
    assert code == 0 and json.loads(captured.out)["status"] == "SUCCEEDED"
    assert json.loads(captured.out)["input"]["prompt_mode"] == "skipped"
    assert "result:" in captured.err


def test_repair_replay_is_offline(monkeypatch, tmp_path, fixture_dir):
    _install_runner(monkeypatch, FakeDocker([MISSING_MAKE, MISSING_MAKE, BUILD_OK], [RUN_OK]))
    code = main(["repair", "--fixture-dir", str(fixture_dir), "--out", str(tmp_path / "replay"), "--replay"])
    result = json.loads((tmp_path / "replay" / "result.json").read_text(encoding="utf-8"))
    assert code == 0 and len(result["rounds"]) == 2 and result["input"]["prompt_mode"] == "skipped"


def test_repair_failure_returns_1(monkeypatch, tmp_path, fixture_dir):
    _install_runner(monkeypatch, FakeDocker([MISSING_MAKE, MISSING_MAKE]))
    assert main([
        "repair", "--fixture-dir", str(fixture_dir), "--out", str(tmp_path / "fail"),
        "--candidate", str(fixture_dir / "Dockerfile.reference"), "--max-iterations", "1",
    ]) == 1


def test_repair_missing_configuration_returns_2(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("draft.cli._repo_root", lambda: tmp_path)
    for name in ("LLM_API_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert main(["repair"]) == 2
    assert "configuration error" in capsys.readouterr().err


@pytest.mark.parametrize("options", [[], ["--max-iterations", "0"], ["--build-timeout", "nan"]])
def test_repair_bad_input_returns_2(tmp_path, options):
    assert main(["repair", "--fixture-dir", str(tmp_path / "missing"), "--candidate", str(tmp_path / "missing-file"), *options]) == 2


def test_repair_source_options_are_exclusive():
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["repair", "--candidate", "candidate", "--replay"])
    assert exc.value.code == 2


def test_repair_environment_only_config_is_supported(monkeypatch, tmp_path):
    from draft.pipeline import load_repair_llm

    monkeypatch.setenv("LLM_API_URL", "https://example.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "placeholder")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    client = load_repair_llm(env_file=tmp_path / "absent.env")
    assert client.config.model == "test-model"
