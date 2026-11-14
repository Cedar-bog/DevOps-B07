import json

from mdfixer.smoke import run_smoke


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
