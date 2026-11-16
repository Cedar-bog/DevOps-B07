from pathlib import Path

import pytest

from mdfixer.accept import accept_candidate
from mdfixer.guard import RepairError, read_source
from mdfixer.rollback import rollback_source

FIXED = Path(__file__).resolve().parents[3] / "fixtures" / "e10" / "Makefile.fixed"


def test_accept_writes_candidate_then_rollback_restores(e10_project):
    root, _ = e10_project()
    original = (root / "Makefile").read_bytes()
    candidate = FIXED.read_text(encoding="utf-8")
    expected = read_source(root, "Makefile").sha256

    accepted = accept_candidate(root, candidate, expected_sha256=expected)
    assert accepted.accepted
    assert (root / "Makefile").read_text(encoding="utf-8") == candidate

    restored = rollback_source(root, original)
    assert restored.ok
    assert restored.restored
    assert (root / "Makefile").read_bytes() == original


def test_accept_rejects_stale_snapshot(e10_project):
    root, _ = e10_project()
    candidate = FIXED.read_text(encoding="utf-8")
    with pytest.raises(RepairError) as error:
        accept_candidate(root, candidate, expected_sha256="0" * 64)
    assert error.value.code == "STALE_REPORT"
    assert (root / "Makefile").read_text(encoding="utf-8").startswith("CC      := gcc")
