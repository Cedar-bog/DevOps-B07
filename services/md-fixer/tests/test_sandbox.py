from mdfixer.gate import INVALID_PATCH
from mdfixer.patch import generate_patch
from mdfixer.sandbox import apply_candidate, create_sandbox, remove_sandbox


def test_sandbox_applies_candidate_without_touching_source(e10_project, snapshot):
    root, report = e10_project()
    before = snapshot(root)
    sandbox = create_sandbox(root)
    try:
        generated = generate_patch(
            root, report, makefile="Makefile", commit=report["repository"]["commit"]
        )
        result = apply_candidate(sandbox, generated.git_patch)
        assert result.ok
        assert "config.h" in (sandbox / "Makefile").read_text(encoding="utf-8")
        assert snapshot(root) == before
    finally:
        remove_sandbox(sandbox)


def test_sandbox_rejects_invalid_patch(e10_project):
    root, _ = e10_project()
    sandbox = create_sandbox(root)
    try:
        result = apply_candidate(sandbox, INVALID_PATCH)
        assert not result.ok
        assert result.error
    finally:
        remove_sandbox(sandbox)


def test_sandbox_does_not_copy_env_files(e10_project):
    root, _ = e10_project()
    (root / ".env").write_text("TEST_ONLY_MARKER=1\n", encoding="utf-8")
    (root / ".env.local").write_text("TEST_ONLY_MARKER=2\n", encoding="utf-8")
    sandbox = create_sandbox(root)
    try:
        assert not (sandbox / ".env").exists()
        assert not (sandbox / ".env.local").exists()
        assert (root / ".env").exists()
    finally:
        remove_sandbox(sandbox)
