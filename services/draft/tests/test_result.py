import json
from datetime import datetime, timezone

from draft.result import RoundRecord, new_run_dir, prepare_project, write_result, write_round


def test_run_directories_do_not_overwrite(tmp_path):
    now = datetime(2026, 10, 30, 2, 11, 7, tzinfo=timezone.utc)
    first = new_run_dir(tmp_path, now=now)
    second = new_run_dir(tmp_path, now=lambda: now)
    assert first.root.name == "2026-10-30T02-11-07Z"
    assert second.root.name == first.root.name + "-1"
    assert first.iter_dir(2).name == "iter-02"


def test_copy_does_not_modify_fixture_or_copy_env(tmp_path, fixture_dir):
    (fixture_dir / ".env").write_text("private settings", encoding="utf-8")
    baseline = (fixture_dir / "Dockerfile.broken").read_bytes()
    paths = new_run_dir(tmp_path / "runs")
    prepare_project(paths, fixture_dir)
    (paths.project / "Dockerfile.broken").write_text("changed", encoding="utf-8")
    assert (fixture_dir / "Dockerfile.broken").read_bytes() == baseline
    assert not (paths.project / ".env").exists()


def test_result_and_round_are_json_and_replace_previous_summary(tmp_path):
    paths = new_run_dir(tmp_path)
    payload = {"schema_version": "1.0", "service": "draft", "rounds": [], "status": "FAILED"}
    target = write_result(paths, payload)
    payload["status"] = "SUCCEEDED"
    write_result(paths, payload)
    assert json.loads(target.read_text(encoding="utf-8")) == payload
    record = RoundRecord(1, "digest", {}, [], {"exit_code": None}, None, {})
    assert json.loads(write_round(paths, record).read_text(encoding="utf-8"))["iteration"] == 1
