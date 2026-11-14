import json
import subprocess
from copy import deepcopy

import pytest

from mdfixer.guard import RepairError
from mdfixer.makefile import parse_makefile
from mdfixer.patch import generate_patch
from mdfixer.plan import rejection_from_error


@pytest.mark.parametrize("style", ["target", "macro", "hybrid"])
def test_patch_matches_independent_e3_reference(project, fixture_root, snapshot, style):
    root, report = project(style)
    before = snapshot(root)
    result = generate_patch(root, report)
    reference = (fixture_root / "styles" / style / "reference.patch").read_text().splitlines(keepends=True)
    expected = "diff --git a/Makefile b/Makefile\n--- a/Makefile\n+++ b/Makefile\n" + "".join(reference[2:])
    assert result.git_patch == expected
    assert snapshot(root) == before
    assert result.plan.source_sha256
    assert result.plan.commit == report["repository"]["commit"]
    assert result.plan.ignored_findings == ("REDUNDANT-main-o-unused-h",)
    assert result.plan.changes[0].style == {"target": "direct", "macro": "macro", "hybrid": "hybrid"}[style]
    json.dumps(result.to_payload())
    # Syntax/applicability only; no candidate is applied to the source project.
    check = subprocess.run(["git", "-C", str(root), "apply", "--check", "-"], input=result.git_patch.encode(), capture_output=True)
    assert check.returncode == 0, check.stderr.decode()


def test_existing_redundant_edge_and_recipe_are_retained(project):
    root, report = project()
    result = generate_patch(root, report)
    assert "main.o: main.c unused.h config.h\n" in result.candidate_text
    assert "\t$(CC) $(CFLAGS) -c $< -o $@\n" in result.candidate_text
    assert "-main.o: main.c unused.h\n+main.o: main.c unused.h config.h\n" in result.git_patch


def test_comments_continuations_and_order_only_are_preserved(project):
    text = "main.o: main.c \\\n unused.h | build # retain\n\tcc -c $< -o $@\n"
    root, report = project(text=text)
    result = generate_patch(root, report)
    assert result.candidate_text == "main.o: main.c \\\n unused.h config.h | build # retain\n\tcc -c $< -o $@\n"


def test_shared_macro_is_rejected_without_changes(project, snapshot):
    root, report = project(text="DEPS = main.c unused.h\nmain.o: $(DEPS)\nother.o: $(DEPS)\n")
    before = snapshot(root)
    with pytest.raises(RepairError) as caught:
        generate_patch(root, report)
    assert caught.value.code == "UNSUPPORTED_TARGET"
    assert snapshot(root) == before


def test_shared_hybrid_variable_is_retained_and_target_only_is_patched(project):
    root, report = project(text="HEADERS = unused.h\nmain.o: main.c $(HEADERS)\nother.o: other.c $(HEADERS)\n")
    result = generate_patch(root, report)
    assert result.candidate_text == "HEADERS = unused.h\nmain.o: main.c $(HEADERS) config.h\nother.o: other.c $(HEADERS)\n"
    assert result.plan.changes[0].variable is None
    assert result.plan.changes[0].affected_targets == ("main.o",)


def test_shared_variable_can_change_when_all_affected_targets_request_edge(project):
    root, report = project(text="DEPS = main.c unused.h\nmain.o: $(DEPS)\nother.o: $(DEPS)\n")
    other = deepcopy(report["findings"][0])
    other.update(finding_id="other", target="other.o", position={"file": "Makefile", "line": 3, "declaration": "other.o: $(DEPS)"})
    report["findings"].append(other)
    result = generate_patch(root, report)
    assert result.candidate_text.count("config.h") == 1
    assert len(result.plan.changes) == 2
    assert result.plan.changes[0].affected_targets == ("main.o", "other.o")


def test_dependency_variable_used_by_clean_is_not_expanded(project):
    root, report = project(text="DEPS = main.c unused.h\nmain.o: $(DEPS)\nclean:\n\trm -f $(DEPS)\n")
    with pytest.raises(RepairError, match="private variable"):
        generate_patch(root, report)


def test_multiple_missing_edges_duplicate_edges_and_no_op(project):
    root, report = project()
    # main.c already exists in the declaration, so it must not be added twice.
    existing = deepcopy(report["findings"][0])
    existing.update(finding_id="already", dependency="main.c")
    duplicate = deepcopy(report["findings"][0])
    duplicate["finding_id"] = "duplicate"
    report["findings"].extend([existing, duplicate])
    result = generate_patch(root, report)
    assert len(result.plan.changes) == 2
    assert result.candidate_text.count("config.h") == 1
    assert result.plan.changes[1].changed is False
    assert generate_patch(root, report) == result


def test_empty_or_redundant_only_reports_produce_empty_patch(project):
    root, report = project()
    report["findings"] = report["findings"][1:]
    result = generate_patch(root, report)
    assert result.git_patch == ""
    assert result.plan.changes == ()


@pytest.mark.parametrize("text", ["main.o: main.c\nmain.o: unused.h\n"])
def test_duplicate_target_declarations_are_rejected(project, snapshot, text):
    root, report = project(text=text)
    before = snapshot(root)
    with pytest.raises(RepairError) as caught:
        generate_patch(root, report)
    assert caught.value.code == "UNSUPPORTED_TARGET"
    assert snapshot(root) == before


def test_multi_target_rule_is_patched_once_for_all_targets(project, snapshot):
    root, report = project(text="main.o other.o: main.c\n\tcc -c $< -o $@\n")
    before = snapshot(root)
    result = generate_patch(root, report)
    assert result.candidate_text == "main.o other.o: main.c config.h\n\tcc -c $< -o $@\n"
    assert result.plan.changes[0].affected_targets == ("main.o", "other.o")
    assert snapshot(root) == before


def test_multi_target_macro_is_edited_once_for_all_targets(project, snapshot):
    root, report = project(text="DEPS = main.c\nmain.o other.o: $(DEPS)\n")
    before = snapshot(root)
    result = generate_patch(root, report)
    assert result.candidate_text == "DEPS = main.c config.h\nmain.o other.o: $(DEPS)\n"
    assert result.plan.changes[0].variable == "DEPS"
    assert result.plan.changes[0].affected_targets == ("main.o", "other.o")
    assert snapshot(root) == before


def test_multi_target_variable_targets_are_patched_together(project, snapshot):
    root, report = project(text="BINS = app tool\n$(BINS): main.c\n")
    report["findings"][0]["target"] = "tool"
    report["findings"][0]["position"] = {
        "file": "Makefile",
        "line": 2,
        "declaration": "$(BINS): main.c",
    }
    before = snapshot(root)
    result = generate_patch(root, report)
    assert result.candidate_text == "BINS = app tool\n$(BINS): main.c config.h\n"
    assert result.plan.changes[0].affected_targets == ("app", "tool")
    assert snapshot(root) == before


def test_crlf_candidate_retains_newlines_and_patch_uses_lf(project):
    root, report = project(text="main.o: main.c unused.h\r\n\tcc -c $< -o $@\r\n")
    result = generate_patch(root, report)
    assert result.candidate_text == "main.o: main.c unused.h config.h\r\n\tcc -c $< -o $@\r\n"
    assert "\r" not in result.git_patch
    check = subprocess.run(["git", "-C", str(root), "apply", "--check", "-"], input=result.git_patch.encode(), capture_output=True)
    assert check.returncode == 0, check.stderr.decode()


@pytest.mark.parametrize("definition", [
    "DEPS := main.c | unused.h",
    "DEPS = main.c | \\\n unused.h",
    "ORDER = | unused.h\nDEPS = main.c $(ORDER)",
])
def test_missing_is_inserted_as_normal_dependency_inside_macros(project, snapshot, definition):
    root, report = project(text=definition + "\nmain.o: $(DEPS)\n\tcc -c $< -o $@\n")
    before = snapshot(root)
    result = generate_patch(root, report)
    rule = parse_makefile(result.candidate_text).rules[0]
    assert rule.prerequisites == ("main.c", "config.h")
    assert rule.order_only == ("unused.h",)
    assert result.plan.changes[0].style == "macro"
    assert snapshot(root) == before


def test_make_confirms_missing_dependency_is_before_order_only_separator(project, make_query):
    root, report = project(text="DEPS := main.c | unused.h\nmain.o: $(DEPS)\n\tcc -c $< -o $@\n")
    result = generate_patch(root, report)
    database = make_query(root, result.candidate_text)
    assert "main.o: main.c config.h | unused.h" in database.splitlines()


@pytest.mark.parametrize("recipe", ["cc -c $S -o $@", "cc -c $(S) -o $@", "cc -c ${S} -o $@", "cc -c $($(WHICH)) -o $@"])
def test_recipe_used_or_computed_macro_is_not_modified(project, snapshot, recipe):
    root, report = project(text="S = main.c\nWHICH = S\nmain.o: $(S)\n\t" + recipe + "\n")
    before = snapshot(root)
    with pytest.raises(RepairError) as caught:
        generate_patch(root, report)
    assert caught.value.code == "UNSUPPORTED_TARGET"
    assert snapshot(root) == before


def test_hybrid_rule_falls_back_without_changing_short_recipe_reference(project, make_query, snapshot):
    root, report = project(text="S = main.c\nmain.o: unused.h $(S)\n\tcc -c $S -o $@\n")
    before = snapshot(root)
    result = generate_patch(root, report)
    assert "S = main.c\n" in result.candidate_text
    assert "main.o: unused.h $(S) config.h\n" in result.candidate_text
    assert make_query(root, result.candidate_text, preview=True).strip() == "cc -c main.c -o main.o"
    assert snapshot(root) == before


def test_escaped_shell_reference_does_not_block_a_safe_macro_edit(project):
    root, report = project(text="S = main.c\nmain.o: $(S)\n\techo $$S\n")
    assert "S = main.c config.h\n" in generate_patch(root, report).candidate_text


def test_repair_payload_requires_recheck_and_keeps_internal_codes_out(project):
    root, report = project()
    result = generate_patch(root, report)
    payload = result.to_repair_payload({
        "build": "SUCCEEDED: make",
        "test": "SUCCEEDED: ./hello",
        "detection": "SUCCEEDED: no MISSING findings remain",
    })
    assert set(payload) == {"git_patch", "style_description", "recheck", "rejected_candidates"}
    assert payload["rejected_candidates"] == []
    assert set(payload["recheck"]) == {"build", "test", "detection"}

    with pytest.raises(ValueError):
        result.to_repair_payload({})

    rejection = rejection_from_error(RepairError("UNSUPPORTED_TARGET", "target is not supported"), patch="diff")
    assert rejection.to_payload() == {
        "patch": "diff",
        "reason": "UNSUPPORTED_TARGET: target is not supported",
    }


def test_collect_rejections_keeps_supported_changes(project, snapshot):
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
    before = snapshot(root)

    result = generate_patch(root, report, collect_rejections=True)

    assert "main.o: main.c config.h\n" in result.candidate_text
    assert "bad.o: $(DEPS)\n" in result.candidate_text
    assert len(result.rejected_candidates) == 1
    assert result.rejected_candidates[0].code == "UNSUPPORTED_TARGET"
    assert result.rejected_candidates[0].to_payload()["reason"].startswith("UNSUPPORTED_TARGET:")
    assert snapshot(root) == before
