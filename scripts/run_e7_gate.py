#!/usr/bin/env python3
"""Run the E7 offline test suite and draft smoke scenario with a summary."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="Python executable")
    parser.add_argument("--skip-smoke", action="store_true", help="run unit tests only")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    test_temp = root / "work" / "e7-pytest-temp"
    test_temp.mkdir(parents=True, exist_ok=True)
    steps = [("tests", [args.python, "-m", "pytest", "services/draft/tests", "-q", "--basetemp", str(test_temp / "pytest")])]
    if not args.skip_smoke:
        steps.append(("draft-smoke", [args.python, "-m", "draft.cli", "smoke", "--fixture-dir", "fixtures/draft", "--docker", str(root / "scripts" / "remote-docker.sh")]))

    failures = 0
    for name, command in steps:
        print(f"==> {name}: {' '.join(command)}", flush=True)
        try:
            env = dict(os.environ)
            draft_path = str(root / "services" / "draft")
            existing = env.get("PYTHONPATH")
            env["PYTHONPATH"] = draft_path if not existing else draft_path + os.pathsep + existing
            if name == "tests":
                env.update(TMP=str(test_temp), TEMP=str(test_temp), TMPDIR=str(test_temp))
            result = subprocess.run(command, cwd=root, check=False, env=env)
            code = result.returncode
        except OSError as exc:
            print(f"could not start: {exc}", file=sys.stderr)
            code = 127
        state = "PASS" if code == 0 else "FAIL"
        print(f"{state} {name} exit_code={code}", flush=True)
        failures += code != 0
    print(f"E7 summary: {len(steps) - failures}/{len(steps)} steps passed; exit_code={int(failures != 0)}")
    return int(failures != 0)


if __name__ == "__main__":
    raise SystemExit(main())
