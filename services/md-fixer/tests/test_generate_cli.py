import json
from copy import deepcopy

from mdfixer.cli import main
from mdfixer.llm import LLMAdapter


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


def test_generate_can_wire_llm_from_env(monkeypatch, project, tmp_path, capsys):
    calls = []

    class FakeAdapter:
        pass

    def fake_from_env(cls):
        calls.append(True)
        return FakeAdapter()

    monkeypatch.setattr(LLMAdapter, "from_env", classmethod(fake_from_env))
    root, report = project()
    report_path = _report_file(tmp_path, report)

    code = main([
        "generate", "--project", str(root), "--report", str(report_path),
        "--llm-from-env", "--json",
    ])

    assert code == 0
    assert calls == [True]
    assert json.loads(capsys.readouterr().out)["git_patch"]


def test_generate_collects_rejections(project, tmp_path, capsys):
    text = (
        "DEPS = main.c unused.h\n"
        "main.o: main.c\n"
        "bad.o: $(DEPS)\n"
        "clean:\n"
        "\trm -f $(DEPS)\n"
    )
    root, report = project(text=text)
    bad = deepcopy(report["findings"][0])
    bad.update(
        finding_id="bad-target",
        target="bad.o",
        position={"file": "Makefile", "line": 3, "declaration": "bad.o: $(DEPS)"},
    )
    report["findings"].append(bad)
    report_path = _report_file(tmp_path, report)

    code = main([
        "generate", "--project", str(root), "--report", str(report_path),
        "--collect-rejections", "--json",
    ])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert "main.o: main.c config.h" in payload["git_patch"]
    assert payload["rejected_candidates"][0]["reason"].startswith("UNSUPPORTED_TARGET:")
