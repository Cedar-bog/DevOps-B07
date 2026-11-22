"""E10 incremental rebuild and declaration recheck.

The incremental check changes one header without cleaning, requires Make to
rebuild the affected object, and compares the new program output. The static
recheck then confirms that every consumed MISSING edge is declared while every
ignored REDUNDANT edge is still present.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from .build import CommandResult, ProgramResult, run_make, run_program
from .guard import RepairError, project_file
from .makefile import Makefile, parse_makefile
from .report import Finding


@dataclass(frozen=True)
class IncrementalResult:
    ok: bool
    header_change: bool
    rebuilt: bool
    build: CommandResult
    program: ProgramResult
    log: str
    error: str | None = None

    def to_payload(self) -> dict:
        return {
            "ok": self.ok,
            "header_change": self.header_change,
            "rebuilt": self.rebuilt,
            "build_ok": self.build.ok,
            "program_ok": self.program.ok,
            "observed_output": self.program.output.strip(),
            "expected_output": self.program.expected,
            "error": self.error,
        }


@dataclass(frozen=True)
class DeclarationCheck:
    ok: bool
    no_missing: bool
    redundant_preserved: bool
    missing_remaining: tuple[tuple[str, str], ...] = ()
    redundant_missing: tuple[tuple[str, str], ...] = ()
    error: str | None = None

    def to_payload(self) -> dict:
        return {
            "ok": self.ok,
            "no_missing": self.no_missing,
            "redundant_preserved": self.redundant_preserved,
            "missing_remaining": [list(edge) for edge in self.missing_remaining],
            "redundant_missing": [list(edge) for edge in self.redundant_missing],
            "error": self.error,
        }


def advance_header(
    header: Path | str, macro: str = "VALUE", old_value: str = "1", new_value: str = "2"
) -> bool:
    """Replace one macro value in a header; return whether a change was made."""
    path = Path(header)
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise RepairError("MISSING_INPUT", "cannot read header for incremental check") from exc
    pattern = re.compile(
        r"(?m)^([ \t]*#[ \t]*define[ \t]+"
        + re.escape(macro)
        + r"[ \t]+)"
        + re.escape(str(old_value))
        + r"\b"
    )
    updated, count = pattern.subn(r"\g<1>" + str(new_value), text, count=1)
    if not count:
        return False
    path.write_bytes(updated.encode("utf-8"))
    return True


def _object_mtimes(root: Path) -> dict[str, int]:
    return {
        path.relative_to(root).as_posix(): path.stat().st_mtime_ns
        for path in root.rglob("*.o")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def run_incremental(
    sandbox: Path | str,
    *,
    header: str = "config.h",
    macro: str = "VALUE",
    old_value: str = "1",
    new_value: str = "2",
    target: str = "app",
    program: str = "app",
    timeout: int = 120,
    env: dict[str, str] | None = None,
    settle_seconds: float = 1.05,
) -> IncrementalResult:
    """Modify a header without cleaning, then require a rebuild and new output."""
    root = Path(sandbox).resolve()
    header_path = project_file(root, header)
    before = _object_mtimes(root)
    time.sleep(max(0.0, settle_seconds))
    try:
        changed = advance_header(header_path, macro, old_value, new_value)
    except RepairError as exc:
        build = CommandResult(False, None, ("make", target), error=str(exc))
        program_result = ProgramResult(False, None, "", str(new_value), program)
        return IncrementalResult(False, False, False, build, program_result, "", str(exc))
    if not changed:
        error = f"{header} does not contain {macro} {old_value}"
        build = CommandResult(False, None, ("make", target), error=error)
        program_result = ProgramResult(False, None, "", str(new_value), program)
        return IncrementalResult(False, False, False, build, program_result, "", error)

    build = run_make(root, target, timeout=timeout, env=env)
    after = _object_mtimes(root)
    rebuilt = any(before.get(name) != mtime or name not in before for name, mtime in after.items())
    program_result = (
        run_program(root, program, new_value, timeout=timeout, env=env)
        if build.ok
        else ProgramResult(False, None, "", str(new_value), program, error="build failed")
    )
    ok = build.ok and rebuilt and program_result.ok
    error = None
    if not build.ok:
        error = (build.stderr or build.stdout).strip() or "incremental build failed"
    elif not rebuilt:
        error = "header change did not rebuild any object"
    elif not program_result.ok:
        error = program_result.error
    log = "\n".join(
        [f"# changed {header}: {macro} {old_value} -> {new_value}", build.log, program_result.log]
    )
    return IncrementalResult(ok, build.ok and program_result.ok and rebuilt, rebuilt, build, program_result, log, error)


def _declares(
    document: Makefile, target: str, dependency: str,
    dep_edges: dict[str, set[str]] | None = None,
) -> bool:
    if dep_edges is not None and dependency in dep_edges.get(target, set()):
        return True
    for rule in document.rules:
        if target in rule.targets and dependency in rule.prerequisites:
            return True
        stem = rule.stem(target)
        if rule.implicit and stem is not None:
            if dependency in {item.replace("%", stem) for item in rule.prerequisites}:
                return True
    return False


def depfile_edges(root: Path | str) -> dict[str, set[str]]:
    """Read compiler-generated .d files as declared edges, without running Make."""
    base = Path(root).resolve()
    edges: dict[str, set[str]] = {}
    for path in base.rglob("*.d"):
        if ".git" in path.relative_to(base).parts or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = text.replace("\\\n", " ")
        for line in text.splitlines():
            if ":" not in line or line.lstrip().startswith("#"):
                continue
            targets, dependencies = line.split(":", 1)
            names = targets.split()
            if not names:
                continue
            for target in names:
                target = target.replace("\\", "/")
                if target.startswith("./"):
                    target = target[2:]
                edges.setdefault(target, set()).update(
                    item.replace("\\", "/").removeprefix("./")
                    for item in dependencies.split()
                )
    return edges


def check_declarations(
    makefile_text: str,
    missing: tuple[Finding, ...],
    redundant: tuple[tuple[str, str], ...] = (),
    *,
    sandbox: Path | str | None = None,
) -> DeclarationCheck:
    try:
        document = parse_makefile(makefile_text, allow_includes=True)
    except RepairError as exc:
        return DeclarationCheck(False, False, False, error=f"{exc.code}: {exc}")
    dep_edges = depfile_edges(sandbox) if sandbox is not None else None
    remaining = tuple(
        (finding.target, finding.dependency)
        for finding in missing
        if not _declares(document, finding.target, finding.dependency, dep_edges)
    )
    redundant_missing = tuple(
        edge for edge in redundant if not _declares(document, *edge, dep_edges)
    )
    no_missing = not remaining
    redundant_preserved = not redundant_missing
    return DeclarationCheck(
        no_missing and redundant_preserved,
        no_missing,
        redundant_preserved,
        remaining,
        redundant_missing,
    )
