import shutil

import pytest

from mdfixer.build import clean_build, find_make, run_program
from mdfixer.guard import read_source
from mdfixer.makefile import parse_makefile
from mdfixer.patch import generate_patch
from mdfixer.recheck import advance_header, check_declarations, run_incremental
from mdfixer.report import validate_report
from mdfixer.sandbox import apply_candidate, create_sandbox, remove_sandbox


def _require_toolchain():
    try:
        find_make()
    except RuntimeError:
        pytest.skip("GNU Make is unavailable")
    if not shutil.which("gcc"):
        pytest.skip("gcc is unavailable")


def test_clean_build_and_program(e10_project):
    _require_toolchain()
    root, _ = e10_project()
    result = clean_build(root)
    assert result.ok, result.log
    program = run_program(root, "app", "1")
    assert program.ok, program.log
    assert program.output.strip() == "1"


def test_incremental_rebuild_and_declaration_recheck(e10_project):
    _require_toolchain()
    root, report = e10_project()
    generated = generate_patch(
        root, report, makefile="Makefile", commit=report["repository"]["commit"]
    )
    sandbox = create_sandbox(root)
    try:
        applied = apply_candidate(sandbox, generated.git_patch)
        assert applied.ok, applied.error
        incremental = run_incremental(sandbox)
        assert incremental.ok, incremental.error
        assert incremental.rebuilt
        assert incremental.program.output.strip() == "2"
        text = (sandbox / "Makefile").read_text(encoding="utf-8")
        source = read_source(root, "Makefile")
        validated = validate_report(
            report,
            source,
            parse_makefile(source.text),
            configuration_id="e10-test",
        )
        declaration = check_declarations(
            text, validated.missing, (("main.o", "unused.h"),)
        )
        assert declaration.ok, declaration.to_payload()
    finally:
        remove_sandbox(sandbox)


def test_advance_header_reports_no_match(tmp_path):
    header = tmp_path / "config.h"
    header.write_text("#define OTHER 1\n", encoding="utf-8")
    assert not advance_header(header)
    assert header.read_text(encoding="utf-8") == "#define OTHER 1\n"
