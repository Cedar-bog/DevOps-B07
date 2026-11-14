import json
import subprocess
from copy import deepcopy

import pytest

from mdfixer.guard import RepairError
from mdfixer.makefile import parse_makefile
from mdfixer.patch import generate_patch


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


@pytest.mark.parametrize("text", ["main.o other.o: main.c\n", "main.o: main.c\nmain.o: unused.h\n"])
def test_unsupported_target_does_not_modify_source(project, snapshot, text):
    # Construct the first declaration manually for the multi-target input.
    root, report = project(text=text.replace("main.o other.o", "main.o"))
    if "other.o" in text:
        (root / "Makefile").write_text(text)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=E9 Test", "-c", "user.email=e9@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "multi target"], check=True, capture_output=True)
        sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"]).decode().strip()
        report["repository"]["commit"] = sha
        for finding in report["findings"]:
            finding["commit"] = sha
        report["findings"][0]["position"]["declaration"] = text.splitlines()[0]
    before = snapshot(root)
    with pytest.raises(RepairError) as caught:
        generate_patch(root, report)
    assert caught.value.code == "UNSUPPORTED_TARGET"
    assert snapshot(root) == before


def test_crlf_candidate_retains_newlines_and_patch_uses_lf(project):
    root, report = project(text="main.o: main.c unused.h\r\n\tcc -c $< -o $@\r\n")
    result = generate_patch(root, report)
    assert result.candidate_text == "main.o: main.c unused.h config.h\r\n\tcc -c $< -o $@\r\n"
    assert "\r" not in result.git_patch
    check = subprocess.run(["git", "-C", str(root), "apply", "--check", "-"], input=result.git_patch.encode(), capture_output=True)
    assert check.returncode == 0, check.stderr.decode()
