import csv
import json
import shutil
from pathlib import Path

import pytest

from mdfixer.build import find_make
from mdfixer.gate import run_gate, run_transaction
from mdfixer.result import scenario_passed

FIXED = Path(__file__).resolve().parents[3] / "fixtures" / "e10" / "Makefile.fixed"
BROKEN_C_PATCH = (
    "diff --git a/main.c b/main.c\n"
    "--- a/main.c\n"
    "+++ b/main.c\n"
    "@@ -1,5 +1,5 @@\n"
    " #include <stdio.h>\n"
    ' #include "config.h"\n'
    " \n"
    "-int main(void)\n"
    "+int main(void\n"
    " {\n"
)
NEW_MD_PATCH = (
    "diff --git a/Makefile b/Makefile\n"
    "--- a/Makefile\n"
    "+++ b/Makefile\n"
    "@@ -11,7 +11,7 @@\n"
    " $(TARGET): $(OBJS)\n"
    " \t$(CC) $(CFLAGS) -o $@ $^\n"
    " \n"
    "-main.o: main.c unused.h\n"
    "+main.o: main.c config.h\n"
    " \t$(CC) $(CFLAGS) -c $< -o $@\n"
    " \n"
    " clean:\n"
)


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
    assert reject_test["checks"]["build"] is True
    assert reject_test["checks"]["test"] is False
    assert reject_test["checks"]["header_change"] is True
    assert reject_test["source_unchanged"] is True
    assert reject_test["rollback"] is True

    reject_patch = scenarios["reject-patch"]["validation"]
    assert reject_patch["accepted"] is False
    assert reject_patch["checks"]["patch_applies"] is False
    assert reject_patch["source_unchanged"] is True
    assert reject_patch["rollback"] is True

    gate = json.loads((output / "gate.json").read_text(encoding="utf-8"))
    assert gate["passed"] is True
    assert gate["exit_code"] == 0
    assert gate["rows"] == 3
    for scenario in ("accept", "reject-test", "reject-patch"):
        assert (output / scenario / "validation.json").is_file()
        assert (output / scenario / "command.json").is_file()
        assert (output / scenario / "stdout.log").is_file()
        assert (output / scenario / "stderr.log").is_file()
        assert (output / scenario / "build.log").is_file()
        assert (output / scenario / "incremental.log").is_file()
        assert (output / scenario / "recheck.json").is_file()

    with (output / "results.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["scenario"] for row in rows] == ["accept", "reject-test", "reject-patch"]
    assert all(row["passed"] == "True" for row in rows)
    assert [row["exit_code"] for row in rows] == ["0", "1", "1"]


def test_compile_failure_rolls_back(e10_project):
    _require_toolchain()
    root, report = e10_project()
    original = (root / "Makefile").read_bytes()
    transaction = run_transaction(
        root, report, scenario="accept", patch=BROKEN_C_PATCH
    )
    validation = transaction.validation
    assert validation["checks"]["patch_applies"] is True
    assert validation["checks"]["build"] is False
    assert validation["accepted"] is False
    assert validation["rollback"] is True
    assert validation["source_unchanged"] is True
    assert validation["error"]
    assert (root / "Makefile").read_bytes() == original


def test_reject_test_requires_successful_build(e10_project):
    _require_toolchain()
    root, report = e10_project()
    original = (root / "Makefile").read_bytes()
    transaction = run_transaction(
        root,
        report,
        scenario="reject-test",
        expected_output="99",
        patch=BROKEN_C_PATCH,
    )
    validation = transaction.validation
    assert validation["checks"]["build"] is False
    assert validation["checks"]["test"] is False
    assert not scenario_passed("reject-test", validation)
    assert (root / "Makefile").read_bytes() == original


def test_recheck_hook_can_reject_a_candidate(e10_project):
    _require_toolchain()
    root, report = e10_project()
    original = (root / "Makefile").read_bytes()

    def external_recheck(sandbox, missing, redundant):
        from mdfixer.recheck import DeclarationCheck
        return DeclarationCheck(False, False, False, error="external BuildChecker rejected")

    transaction = run_transaction(
        root, report, scenario="accept", recheck=external_recheck
    )
    validation = transaction.validation
    assert validation["accepted"] is False
    assert validation["rollback"] is True
    assert validation["source_unchanged"] is True
    assert "external BuildChecker" in validation["error"]
    assert (root / "Makefile").read_bytes() == original


def test_candidate_removing_redundant_edge_rolls_back(e10_project):
    _require_toolchain()
    root, report = e10_project()
    original = (root / "Makefile").read_bytes()
    transaction = run_transaction(
        root, report, scenario="accept", patch=NEW_MD_PATCH
    )
    validation = transaction.validation
    assert validation["checks"]["patch_applies"] is True
    assert validation["checks"]["build"] is True
    assert validation["checks"]["test"] is True
    assert validation["checks"]["header_change"] is True
    assert validation["checks"]["no_missing"] is False
    assert validation["accepted"] is False
    assert validation["rollback"] is True
    assert validation["source_unchanged"] is True
    assert "REDUNDANT" in validation["error"]
    assert (root / "Makefile").read_bytes() == original
