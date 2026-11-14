from pathlib import Path
import subprocess

import pytest

from mdfixer.guard import RepairError, project_file, read_source, relative_path
from mdfixer.patch import generate_patch
from mdfixer.report import load_report


@pytest.mark.parametrize("value", [
    "../outside.h", "/outside.h", "C:/outside.h", "C:outside.h", "a\\b.h",
    "a/../../outside.h", "a/.git/config", ".ENV", ".Env.local", "x/.GIT/config",
    "$(VALUE)", "a;command", "a b.h", "-include", "a/-f", "", None,
])
def test_unsafe_paths_are_rejected(value):
    with pytest.raises(RepairError) as caught:
        relative_path(value)
    assert caught.value.code == "UNSAFE_PATH"


def test_source_identity_requires_git_and_requested_head(tmp_path, project):
    with pytest.raises(RepairError):
        read_source(tmp_path)
    root, report = project()
    with pytest.raises(RepairError) as caught:
        generate_patch(root, report, commit="0" * 40)
    assert caught.value.code == "STALE_REPORT"


def test_modified_source_invalidates_the_report_even_if_makefile_is_unchanged(project, snapshot):
    root, report = project()
    (root / "main.c").write_bytes(b"/* source changed since detection */\n")
    before = snapshot(root)
    with pytest.raises(RepairError) as caught:
        generate_patch(root, report)
    assert caught.value.code == "STALE_REPORT"
    assert snapshot(root) == before


def test_untracked_dependency_is_rejected(project):
    root, report = project()
    subprocess.run(["git", "-C", str(root), "rm", "--cached", "config.h"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=E9 Test", "-c", "user.email=e9@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-qm", "untrack config.h"],
        check=True, capture_output=True,
    )
    sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"]).decode().strip()
    report["repository"]["commit"] = sha
    for finding in report["findings"]:
        finding["commit"] = sha

    with pytest.raises(RepairError) as caught:
        generate_patch(root, report)

    assert caught.value.code == "STALE_REPORT"


def test_report_can_be_read_as_an_explicit_json_input(project, tmp_path):
    import json
    root, report = project()
    path = tmp_path / "input-report.json"
    path.write_text(json.dumps(report))
    assert generate_patch(root, path).git_patch


@pytest.mark.parametrize("text", ["{", "[]", "null"])
def test_invalid_json_inputs(text, tmp_path):
    path = tmp_path / "report.json"
    path.write_text(text)
    with pytest.raises(RepairError) as caught:
        load_report(path)
    assert caught.value.code == "INVALID_REPORT"


def test_env_paths_are_rejected_before_any_content_is_read(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("must not read this file")
    monkeypatch.setattr(Path, "read_text", forbidden)
    with pytest.raises(RepairError) as caught:
        load_report(tmp_path / ".ENV")
    assert caught.value.code == "UNSAFE_PATH"


def test_resolved_dependency_path_cannot_escape(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.h"
    outside.write_text("not an input")
    link = root / "config.h"
    resolve = Path.resolve

    def resolve_link(path, *args, **kwargs):
        return outside if path == link else resolve(path, *args, **kwargs)

    # Exercise containment after link resolution on hosts where creating a
    # real symlink would require administrator privileges.
    monkeypatch.setattr(Path, "resolve", resolve_link)
    with pytest.raises(RepairError) as caught:
        project_file(root, "config.h")
    assert caught.value.code == "UNSAFE_PATH"
