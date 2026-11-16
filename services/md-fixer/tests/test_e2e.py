import csv
import json
import shutil
from pathlib import Path

import pytest

from mdfixer.build import find_make
from mdfixer.gate import run_gate
from mdfixer.result import scenario_passed

FIXED = Path(__file__).resolve().parents[3] / "fixtures" / "e10" / "Makefile.fixed"


def _require_toolchain():
    try:
        find_make()
    except RuntimeError:
        pytest.skip("GNU Make is unavailable")
    if not shutil.which("gcc"):
        pytest.skip("gcc is unavailable")


def test_three_scenario_gate_and_artifacts(e10_project, tmp_path):
    _require_toolchain()
    root, report = e10_project()
    output = tmp_path / "gate"
    result = run_gate(root, report, output_dir=output)

    assert result.passed, result.to_payload()
    assert result.rows == 3
    scenarios = {item["scenario"]: item for item in result.scenarios}
    assert set(scenarios) == {"accept", "reject-test", "reject-patch"}
    assert all(scenario_passed(name, item["validation"]) for name, item in scenarios.items())

    accept = scenarios["accept"]["validation"]
    assert accept["accepted"] is True
    assert accept["source_unchanged"] is False
    assert accept["rollback"] is False
    assert all(accept["checks"].values())
    assert (root / "Makefile").read_text(encoding="utf-8") == FIXED.read_text(encoding="utf-8")

    reject_test = scenarios["reject-test"]["validation"]
    assert reject_test["accepted"] is False
    assert reject_test["checks"]["test"] is False
    assert reject_test["source_unchanged"] is True
    assert reject_test["rollback"] is True

    reject_patch = scenarios["reject-patch"]["validation"]
    assert reject_patch["accepted"] is False
    assert reject_patch["checks"]["patch_applies"] is False
    assert reject_patch["source_unchanged"] is True
    assert reject_patch["rollback"] is True

    gate = json.loads((output / "gate.json").read_text(encoding="utf-8"))
    assert gate["passed"] is True
    assert gate["rows"] == 3
    for scenario in ("accept", "reject-test", "reject-patch"):
        assert (output / scenario / "validation.json").is_file()
        assert (output / scenario / "build.log").is_file()
        assert (output / scenario / "incremental.log").is_file()
        assert (output / scenario / "recheck.json").is_file()

    with (output / "results.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["scenario"] for row in rows] == ["accept", "reject-test", "reject-patch"]
    assert all(row["passed"] == "True" for row in rows)
