import json

from mdfixer.cli import main


def _write_report(tmp_path, report):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def test_validate_reject_patch_cli(e10_project, tmp_path, capsys):
    root, report = e10_project()
    report_path = _write_report(tmp_path, report)
    original = (root / "Makefile").read_bytes()

    code = main(
        [
            "validate",
            "--project", str(root),
            "--report", str(report_path),
            "--only", "reject-patch",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["scenario"] == "reject-patch"
    assert payload["validation"]["checks"]["patch_applies"] is False
    assert payload["validation"]["source_unchanged"] is True
    assert (root / "Makefile").read_bytes() == original


def test_validate_rejects_output_inside_project(e10_project, tmp_path, capsys):
    root, report = e10_project()
    report_path = _write_report(tmp_path, report)

    code = main(
        [
            "validate",
            "--project", str(root),
            "--report", str(report_path),
            "--only", "reject-patch",
            "--out", str(root / "e10-output"),
        ]
    )
    assert code == 2
    assert "outside the project" in capsys.readouterr().err
