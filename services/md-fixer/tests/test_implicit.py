import json
import subprocess
from copy import deepcopy
from types import SimpleNamespace

import pytest

from mdfixer.guard import RepairError
from mdfixer.llm import LLMAdapter
from mdfixer.patch import generate_patch


class FakeClient:
    def __init__(self, reply=None, failure=None):
        self.reply = reply
        self.failure = failure
        self.calls = []

    def complete(self, prompt, *, timeout):
        self.calls.append((prompt, timeout))
        if self.failure:
            raise self.failure
        return self.reply


def complex_project(project):
    root, report = project("implicit")
    # A different recipe needs model-assisted handling rather than the simple
    # .d rewrite. Commit it so report identity remains meaningful.
    text = (root / "Makefile").read_text().replace("-c $< -o $@", "-c $< -o $@ && echo compiled")
    (root / "Makefile").write_bytes(text.encode())
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=E9 Test", "-c", "user.email=e9@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "complex recipe"], check=True, capture_output=True)
    sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"]).decode().strip()
    report["repository"]["commit"] = sha
    for finding in report["findings"]:
        finding["commit"] = sha
    return root, report, text


def test_simple_implicit_patch_matches_e3_reference(project, fixture_root, snapshot):
    root, report = project("implicit")
    before = snapshot(root)
    unused_llm = FakeClient(failure=AssertionError("must stay offline"))
    result = generate_patch(root, report, llm=unused_llm)
    reference = (fixture_root / "styles" / "implicit" / "reference.patch").read_text().splitlines(keepends=True)
    expected = "diff --git a/Makefile b/Makefile\n--- a/Makefile\n+++ b/Makefile\n" + "".join(reference[2:])
    assert result.git_patch == expected
    assert result.plan.changes[0].strategy == "depfile"
    assert snapshot(root) == before
    assert unused_llm.calls == []
    check = subprocess.run(["git", "-C", str(root), "apply", "--check", "-"], input=result.git_patch.encode(), capture_output=True)
    assert check.returncode == 0, check.stderr.decode()


def test_implicit_multiple_missings_share_one_depfile_scheme(project):
    root, report = project("implicit")
    additional = deepcopy(report["findings"][0])
    additional.update(finding_id="other-header", dependency="unused.h")
    report["findings"].append(additional)
    result = generate_patch(root, report)
    assert result.candidate_text.count("-MMD -MP") == 1
    assert result.candidate_text.count("-include") == 1
    assert len(result.plan.changes) == 2


def test_complex_rule_requires_explicit_model_injection(project, snapshot):
    root, report, text = complex_project(project)
    before = snapshot(root)
    with pytest.raises(RepairError) as caught:
        generate_patch(root, report)
    assert caught.value.code == "LLM_REQUIRED"
    assert snapshot(root) == before


@pytest.mark.parametrize("wrapped", [False, True])
def test_complex_candidate_keeps_original_recipe_and_only_requested_edges(project, snapshot, wrapped):
    root, report, text = complex_project(project)
    before = snapshot(root)
    content = json.dumps({"makefile": text + "\nmain.o: config.h\n"})
    reply = SimpleNamespace(content="```json\n" + content + "\n```") if wrapped else content
    client = FakeClient(reply)
    result = generate_patch(root, report, llm=LLMAdapter(client, timeout=3))
    assert result.candidate_text == text + "\nmain.o: config.h\n"
    assert result.plan.changes[0].strategy == "llm_prerequisites"
    assert len(client.calls) == 1 and client.calls[0][1] == 3
    assert "REDUNDANT" not in client.calls[0][0].split("\n")[-1]
    assert snapshot(root) == before


@pytest.mark.parametrize("mode", ["recipe", "extra", "command", "include", "traversal", "empty", "duplicate", "missing"])
def test_untrusted_model_edits_are_rejected_without_writing_source(project, snapshot, mode):
    root, report, text = complex_project(project)
    candidates = {
        "recipe": text.replace("echo compiled", "echo changed") + "main.o: config.h\n",
        "extra": text + "main.o: config.h unused.h\n",
        "command": text + "main.o: config.h\n\trm -f main.c\n",
        "include": text + "main.o: config.h\ninclude /other.mk\n",
        "traversal": text + "main.o: ../config.h\n",
        "empty": text + "main.o:\n",
        "duplicate": text + "main.o: config.h config.h\n",
        "missing": text,
    }
    before = snapshot(root)
    with pytest.raises(RepairError) as caught:
        generate_patch(root, report, llm=FakeClient(json.dumps({"makefile": candidates[mode]})))
    assert caught.value.code in {"UNSUPPORTED_TARGET", "UNSAFE_PATH"}
    assert snapshot(root) == before


@pytest.mark.parametrize("reply", ["not JSON", "{}", "[]", '{"makefile": 1}', '{"makefile": "x", "extra": 1}', None])
def test_bad_model_replies(project, snapshot, reply):
    root, report, text = complex_project(project)
    before = snapshot(root)
    with pytest.raises(RepairError) as caught:
        generate_patch(root, report, llm=FakeClient(reply))
    assert caught.value.code == "LLM_ERROR"
    assert snapshot(root) == before


def test_model_timeout_is_a_bounded_sanitized_rejection(project, snapshot):
    root, report, text = complex_project(project)
    before = snapshot(root)
    with pytest.raises(RepairError) as caught:
        generate_patch(root, report, llm=FakeClient(failure=TimeoutError("provider-private-detail")))
    assert caught.value.code == "LLM_ERROR"
    assert "provider-private-detail" not in str(caught.value)
    assert caught.value.__suppress_context__
    assert snapshot(root) == before


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_model_timeout_settings(timeout):
    with pytest.raises(ValueError):
        LLMAdapter(FakeClient(), timeout=timeout)


def test_reuses_existing_llm_transport_without_env_file(project, monkeypatch):
    from pathlib import Path
    from draft.llm import HTTPResponse
    root, report, text = complex_project(project)
    seen = []

    def no_file_reads(*args, **kwargs):
        raise AssertionError("the adapter must not discover configuration files")

    def transport(request, timeout):
        seen.append((json.loads(request.body), timeout))
        content = json.dumps({"makefile": text + "main.o: config.h\n"})
        return HTTPResponse(200, json.dumps({"choices": [{"message": {"content": content}}]}).encode())

    monkeypatch.setattr(Path, "read_text", no_file_reads)
    adapter = LLMAdapter.from_env(
        environ={"LLM_API_URL": "https://model.example.invalid/v1", "LLM_API_KEY": "test-only", "LLM_MODEL": "test-model"},
        transport=transport, timeout=2,
    )
    result = generate_patch(root, report, llm=adapter)
    assert result.plan.changes[0].strategy == "llm_prerequisites"
    assert len(seen) == 1 and seen[0][1] == 2
    assert seen[0][0]["model"] == "test-model"


def test_implicit_private_variable_names_do_not_clobber_existing_settings(project):
    text = (
        "CC ?= cc\nCFLAGS ?= -O2\nTARGET := hello\nOBJS:=main.o\n"
        "DEPFLAGS = original-flags\nDEPS = unused.h\n"
        "all: $(TARGET)\n$(TARGET): $(OBJS)\n\t$(CC) -o $@ $^\n"
        "%.o: %.c\n\t$(CC) $(CFLAGS) -c $< -o $@\n"
        "clean:\n\trm -f $(TARGET) $(OBJS)\n"
    )
    root, report = project(text=text)
    result = generate_patch(root, report)
    assert "DEPFLAGS = original-flags\nDEPS = unused.h\n" in result.candidate_text
    assert "MDFIXER_DEPFLAGS = -MMD -MP\n" in result.candidate_text
    assert "MDFIXER_DEPS := $(OBJS:.o=.d)\n" in result.candidate_text
    assert "-include $(MDFIXER_DEPS)\n" in result.candidate_text


@pytest.mark.parametrize("definitions", [
    "OBJS = $(OBJECTS)\nOBJECTS = main.o\n",
    "OBJS = $(OBJECTS)\nOBJECTS = $(BASE)\nBASE = main.o\n",
    "BASE := main.o\nOBJS = $(BASE)\n",
])
def test_depfile_initialization_follows_all_object_definitions(project, definitions, snapshot):
    text = (
        "CC ?= cc\nCFLAGS ?= -O2\nTARGET := hello\n" + definitions +
        "all: $(TARGET)\n$(TARGET): $(OBJS)\n\t$(CC) $(CFLAGS) -o $@ $^\n"
        "%.o: %.c\n\t$(CC) $(CFLAGS) -c $< -o $@\n"
        "clean:\n\trm -f $(TARGET) $(OBJS)\n"
    )
    root, report = project(text=text)
    before = snapshot(root)
    result = generate_patch(root, report)
    candidate = result.candidate_text
    assert candidate.index("DEPS :=") > max(candidate.index(line) for line in definitions.splitlines())
    assert candidate.index("-include $(DEPS)") > candidate.index("DEPS :=")
    assert snapshot(root) == before


def test_make_confirms_forward_reference_depfile_is_loaded(project, make_query):
    text = (
        "CC ?= cc\nCFLAGS ?= -O2\nTARGET := hello\n"
        "OBJS = $(OBJECTS)\nOBJECTS = $(BASE)\nBASE = main.o\n"
        "all: $(TARGET)\n$(TARGET): $(OBJS)\n\t$(CC) $(CFLAGS) -o $@ $^\n"
        "%.o: %.c\n\t$(CC) $(CFLAGS) -c $< -o $@\n"
        "clean:\n\trm -f $(TARGET) $(OBJS)\n"
    )
    root, report = project(text=text)
    result = generate_patch(root, report)
    # Supply a known depfile as fixture data, without running a compiler or
    # applying the candidate to the source Makefile.
    (root / "main.d").write_bytes(b"main.o: main.c config.h\n")
    database = make_query(root, result.candidate_text)
    assert "DEPS := main.d" in database.splitlines()
    prerequisites = next(line for line in database.splitlines() if line.startswith("main.o:")).partition(":")[2].split()
    assert set(prerequisites) == {"main.c", "config.h"}


def test_early_clean_keeps_default_goal_and_includes_initialized_depfiles(project, make_query):
    text = (
        "CC ?= cc\nCFLAGS ?= -O2\nTARGET := hello\n"
        "clean:\n\trm -f $(TARGET) $(OBJS)\n"
        "OBJS = $(OBJECTS)\nOBJECTS = main.o\n"
        "all: $(TARGET)\n$(TARGET): $(OBJS)\n\t$(CC) $(CFLAGS) -o $@ $^\n"
        "%.o: %.c\n\t$(CC) $(CFLAGS) -c $< -o $@\n"
    )
    root, report = project(text=text)
    result = generate_patch(root, report)
    (root / "main.d").write_bytes(b"main.o: main.c config.h\n")
    database = make_query(root, result.candidate_text)
    assert ".DEFAULT_GOAL := clean" in database.splitlines()
    assert "DEPS := main.d" in database.splitlines()
    prerequisites = next(line for line in database.splitlines() if line.startswith("main.o:")).partition(":")[2].split()
    assert set(prerequisites) == {"main.c", "config.h"}


def test_generated_names_do_not_capture_existing_substitution_references(project):
    text = (
        "CC ?= cc\nCFLAGS ?= -O2\nTARGET := hello\nOBJS := main.o\n"
        "all: $(TARGET)\n$(TARGET): $(OBJS)\n\t$(CC) -o $@ $^\n"
        "%.o: %.c\n\t$(CC) $(CFLAGS) -c $< -o $@\n"
        "inspect:\n\techo $(DEPS:.o=.d) $(DEPFLAGS:.o=.d)\n"
        "clean:\n\trm -f $(TARGET) $(OBJS)\n"
    )
    root, report = project(text=text)
    candidate = generate_patch(root, report).candidate_text
    assert "MDFIXER_DEPS := $(OBJS:.o=.d)\n" in candidate
    assert "MDFIXER_DEPFLAGS = -MMD -MP\n" in candidate
    assert "\techo $(DEPS:.o=.d) $(DEPFLAGS:.o=.d)\n" in candidate
