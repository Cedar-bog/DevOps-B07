import json
from pathlib import Path

import pytest

from mdfixer.cli import main
from mdfixer.smoke import run_smoke, write_result


def test_smoke_passes_on_fixture(fixture_root):
    result = run_smoke(fixture_root)
    assert result.ok
    assert {check.name for check in result.checks} >= {
        "fixture-dir",
        "fixture-files",
        "report-json",
        "report-findings",
        "report-fields",
    }


def test_smoke_never_follows_report_paths(fixture_root, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("SMOKE-SECRET-MARKER\n", encoding="utf-8")
    payload = json.loads((fixture_root / "fixed-md-report.json").read_text(encoding="utf-8"))
    payload["findings"][0]["position"]["file"] = str(secret)
    report = tmp_path / "report.json"
    report.write_text(json.dumps(payload), encoding="utf-8")

    result = run_smoke(fixture_root, report=report)

    assert result.ok
    assert "SMOKE-SECRET-MARKER" not in json.dumps(result.to_payload())


def test_smoke_rejects_sensitive_report_paths(fixture_root, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("SMOKE-SECRET-MARKER=1\n", encoding="utf-8")

    result = run_smoke(fixture_root, report=env_file)

    assert not result.ok
    assert "SMOKE-SECRET-MARKER" not in json.dumps(result.to_payload())
    report_check = next(check for check in result.checks if check.name == "report-json")
    assert not report_check.ok
    assert "sensitive" in report_check.detail


def test_write_result_rejects_sensitive_paths(fixture_root, tmp_path):
    result = run_smoke(fixture_root)
    target = tmp_path / ".env"

    with pytest.raises(ValueError):
        write_result(result, target)

    assert not target.exists()


def test_smoke_cli_rejects_sensitive_output(fixture_root, tmp_path):
    target = tmp_path / ".env"

    code = main(["smoke", "--output", str(target)])

    assert code == 2
    assert not target.exists()


def test_smoke_cli_rejects_source_output(fixture_root):
    target = Path(__file__).resolve().parents[3] / "fixtures" / "md-fixer" / "main.c"
    before = target.read_bytes()

    code = main(["smoke", "--output", str(target)])

    assert code == 2
    assert target.read_bytes() == before
