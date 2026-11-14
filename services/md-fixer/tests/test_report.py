from copy import deepcopy

import pytest

from mdfixer.guard import RepairError, read_source
from mdfixer.makefile import parse_makefile
from mdfixer.report import validate_report


def validate(root, report, **kwargs):
    source = read_source(root)
    return validate_report(report, source, parse_makefile(source.text), **kwargs)


def test_missing_is_located_redundant_is_ignored(project):
    root, payload = project()
    report = validate(root, payload)
    assert [(f.target, f.dependency) for f in report.missing] == [("main.o", "config.h")]
    assert report.ignored == ("REDUNDANT-main-o-unused-h",)


@pytest.mark.parametrize("key,value,code", [
    ("type", "UNKNOWN", "INVALID_REPORT"),
    ("detector", "UNKNOWN", "INVALID_REPORT"),
    ("commit", "0" * 40, "STALE_REPORT"),
    ("target", "other.o", "UNSUPPORTED_TARGET"),
    ("dependency", "../config.h", "UNSAFE_PATH"),
    ("dependency", "C:/outside.h", "UNSAFE_PATH"),
    ("dependency", "$(shell something)", "UNSAFE_PATH"),
    ("dependency", "absent.h", "MISSING_INPUT"),
])
def test_bad_findings(project, key, value, code):
    root, report = project()
    report["findings"][0][key] = value
    with pytest.raises(RepairError) as caught:
        validate(root, report)
    assert caught.value.code == code


@pytest.mark.parametrize("key,value,code", [
    ("line", True, "INVALID_REPORT"), ("line", 0, "INVALID_REPORT"),
    ("line", 999, "INVALID_REPORT"), ("line", 1, "STALE_REPORT"),
    ("file", "other.mk", "UNSUPPORTED_TARGET"),
    ("file", "../Makefile", "UNSAFE_PATH"),
    ("declaration", "main.o: old.c", "STALE_REPORT"),
])
def test_position_must_match(project, key, value, code):
    root, report = project()
    report["findings"][0]["position"][key] = value
    with pytest.raises(RepairError) as caught:
        validate(root, report)
    assert caught.value.code == code


@pytest.mark.parametrize("field", ["finding_id", "commit", "detector", "position", "dependency"])
def test_required_missing_fields(project, field):
    root, report = project()
    del report["findings"][0][field]
    with pytest.raises(RepairError) as caught:
        validate(root, report)
    assert caught.value.code == "INVALID_REPORT"


def test_duplicate_edges_are_deduplicated(project):
    root, report = project()
    duplicate = deepcopy(report["findings"][0])
    duplicate["finding_id"] = "second-detector"
    duplicate["detector"] = "ECHECKER"
    report["findings"].append(duplicate)
    assert len(validate(root, report).missing) == 1


def test_commit_configuration_and_unknown_fields(project):
    root, report = project()
    with pytest.raises(RepairError, match="configuration"):
        validate(root, report, configuration_id="other")
    report["surprise"] = True
    with pytest.raises(RepairError) as caught:
        validate(root, report)
    assert caught.value.code == "INVALID_REPORT"


def test_local_makefile_edit_is_not_silently_patched(project):
    root, report = project()
    with (root / "Makefile").open("a") as handle:
        handle.write("# local change\n")
    with pytest.raises(RepairError) as caught:
        validate(root, report)
    assert caught.value.code == "STALE_REPORT"


def test_redundant_findings_are_ignored_but_must_have_current_commit(project):
    root, report = project()
    report["findings"][1]["commit"] = "0" * 40
    with pytest.raises(RepairError) as caught:
        validate(root, report)
    assert caught.value.code == "STALE_REPORT"


def test_redundant_findings_still_require_a_position(project):
    root, report = project()
    del report["findings"][1]["position"]
    with pytest.raises(RepairError) as caught:
        validate(root, report)
    assert caught.value.code == "INVALID_REPORT"


def test_top_level_commit_and_actual_implicit_source_are_checked(project):
    root, report = project()
    report["repository"]["commit"] = "0" * 40
    with pytest.raises(RepairError) as caught:
        validate(root, report)
    assert caught.value.code == "STALE_REPORT"
    root, report = project("implicit")
    report["findings"][0]["target"] = "nonexistent.o"
    with pytest.raises(RepairError) as caught:
        validate(root, report)
    assert caught.value.code == "MISSING_INPUT"
