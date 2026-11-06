#!/usr/bin/env python3
"""Run the E7 offline test suite and summarize its exit code."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="Python executable")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]

    failures = 0
    with tempfile.TemporaryDirectory(prefix="e7-pytest-") as temp_dir:
        command = [
            args.python, "-m", "pytest", "services/draft/tests",
            "-q", "--basetemp", str(Path(temp_dir) / "pytest"),
        ]
        print(f"==> tests: {' '.join(command)}", flush=True)
        try:
            env = dict(os.environ)
            draft_path = str(root / "services" / "draft")
            existing = env.get("PYTHONPATH")
            env["PYTHONPATH"] = draft_path if not existing else draft_path + os.pathsep + existing
            env.update(TMP=temp_dir, TEMP=temp_dir, TMPDIR=temp_dir)
            result = subprocess.run(command, cwd=root, check=False, env=env)
            code = result.returncode
        except OSError as exc:
            print(f"could not start: {exc}", file=sys.stderr)
            code = 127

        state = "PASS" if code == 0 else "FAIL"
        print(f"{state} tests exit_code={code}", flush=True)
        failures += code != 0

    print(f"E7 summary: {1 - failures}/1 steps passed; exit_code={int(failures != 0)}")
    return int(failures != 0)


if __name__ == "__main__":
    raise SystemExit(main())
