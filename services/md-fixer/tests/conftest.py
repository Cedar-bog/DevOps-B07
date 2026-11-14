"""Committed temporary projects; the shared E3 fixtures remain untouched."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "fixtures" / "md-fixer"


@pytest.fixture
def fixture_root():
    return FIXTURE


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)
    return result.stdout.decode().strip()


@pytest.fixture
def project(tmp_path):
    def create(style="base", text=None):
        root = tmp_path / f"project-{len(list(tmp_path.iterdir()))}"
        root.mkdir()
        for name in ("main.c", "config.h", "unused.h"):
            shutil.copyfile(FIXTURE / name, root / name)
        original = FIXTURE / "Makefile" if style == "base" else FIXTURE / "styles" / style / "Makefile.before"
        content = original.read_text() if text is None else text
        (root / "Makefile").write_bytes(content.encode())
        git(root, "init", "-q")
        git(root, "add", ".")
        git(root, "-c", "user.name=E9 Test", "-c", "user.email=e9@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "test input")
        commit = git(root, "rev-parse", "HEAD")
        report = json.loads((FIXTURE / "fixed-md-report.json").read_text())
        report["repository"]["commit"] = commit
        for finding in report["findings"]:
            finding["commit"] = commit
        for finding in report["findings"]:
            if finding["type"] == "REDUNDANT":
                # REDUNDANT is ignored; the test project only needs a valid position.
                finding["position"] = {"file": "Makefile", "line": 1}
        finding = report["findings"][0]
        declaration = next(line for line in content.splitlines() if line.startswith(("main.o:", "%.o:")))
        finding["position"] = {"file": "Makefile", "line": content.splitlines().index(declaration) + 1, "declaration": declaration}
        return root, report
    return create


@pytest.fixture
def snapshot():
    def capture(root):
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file() and ".git" not in p.relative_to(root).parts}
    return capture


@pytest.fixture
def make_query(tmp_path):
    """Independent GNU Make oracle: parse/query or preview, never build."""
    executable = next((path for name in ("make", "gmake", "mingw32-make") if (path := shutil.which(name))), None)
    if executable is None:
        pytest.skip("GNU Make is unavailable; pure Python regression tests still run")
    queries = tmp_path / "make-queries"
    queries.mkdir()

    def query(root, text, *, preview=False, target="main.o"):
        path = queries / f"query-{len(list(queries.iterdir()))}.mk"
        path.write_bytes(text.encode())
        # Make's database includes its environment. Supply only OS settings so
        # no credentials enter a test result or failure message.
        environ = {name: os.environ[name] for name in ("SystemRoot", "COMSPEC") if name in os.environ}
        result = subprocess.run(
            [executable, "--no-builtin-rules", "--no-builtin-variables", "--no-print-directory", "-f", str(path), "-n" if preview else "-pq", target],
            cwd=root, env=environ, capture_output=True, timeout=10,
        )
        assert result.returncode in {0, 1}, result.stderr.decode(errors="replace")
        return result.stdout.decode(errors="replace")
    return query
