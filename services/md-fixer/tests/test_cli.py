import json

from mdfixer.cli import main


def _report_file(tmp_path, report):
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def test_generate_prints_candidate_json(project, tmp_path, capsys):
    root, report = project()
    report_path = _report_file(tmp_path, report)

    code = main([
        "generate", "--project", str(root), "--report", str(report_path), "--json",
    ])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["git_patch"].startswith("diff --git a/Makefile b/Makefile")
    assert payload["plan"]["changes"][0]["dependency"] == "config.h"
    assert payload["plan"]["changes"][0]["style"] == "direct"


def test_generate_writes_patch_and_plan_without_touching_source(project, tmp_path, snapshot):
    root, report = project()
    report_path = _report_file(tmp_path, report)
    before = snapshot(root)
    patch_path = tmp_path / "candidate.patch"
    plan_path = tmp_path / "plan.json"

    code = main([
        "generate", "--project", str(root), "--report", str(report_path),
        "--patch-out", str(patch_path), "--plan-out", str(plan_path),
    ])

    assert code == 0
    assert patch_path.read_text(encoding="utf-8").startswith("diff --git")
    assert json.loads(plan_path.read_text(encoding="utf-8"))["changes"][0]["finding_id"]
    assert snapshot(root) == before


def test_generate_refuses_output_inside_project(project, tmp_path):
    root, report = project()
    report_path = _report_file(tmp_path, report)
    target = root / "candidate.patch"

    code = main([
        "generate", "--project", str(root), "--report", str(report_path),
        "--patch-out", str(target),
    ])

    assert code == 2
    assert not target.exists()


def test_generate_reports_stale_commit_as_rejection(project, tmp_path, capsys):
    root, report = project()
    report["repository"]["commit"] = "0" * 40
    report_path = _report_file(tmp_path, report)

    code = main([
        "generate", "--project", str(root), "--report", str(report_path), "--json",
    ])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "REJECTED"
    assert payload["code"] == "STALE_REPORT"
