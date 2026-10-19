"""Unit tests for the DRAFT command line entry point."""

from __future__ import annotations

import json

from draft import __version__
from draft.cli import SERVICE, build_parser, main
from draft.smoke import SmokeError, SmokeResult


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
