"""E10 clean build and program checks inside a sandbox."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    returncode: int | None
    command: tuple[str, ...]
    stdout: str = ""
    stderr: str = ""
    error: str | None = None

    @property
    def log(self) -> str:
        lines = ["$ " + " ".join(self.command)]
        if self.stdout:
            lines.append(self.stdout.rstrip("\n"))
        if self.stderr:
            lines.append(self.stderr.rstrip("\n"))
        if self.error:
            lines.append("error: " + self.error)
        lines.append(f"exit_code: {self.returncode}")
        return "\n".join(lines)


@dataclass(frozen=True)
class BuildResult:
    ok: bool
    clean: CommandResult
    build: CommandResult
    log: str = ""

    def __post_init__(self) -> None:
        if not self.log:
            object.__setattr__(self, "log", self.clean.log + "\n" + self.build.log + "\n")


@dataclass(frozen=True)
class ProgramResult:
    ok: bool
    returncode: int | None
    output: str
    expected: str
    program: str = ""
    path: str = ""
    error: str | None = None

    @property
    def log(self) -> str:
        lines = [f"$ {self.path or self.program}"]
        if self.output:
            lines.append(self.output.rstrip("\n"))
        if self.error:
            lines.append("error: " + self.error)
        lines.append(f"exit_code: {self.returncode}")
        lines.append(f"expected_output: {self.expected}")
        lines.append(f"observed_output: {self.output.strip()}")
        return "\n".join(lines)


def _find(names: tuple[str, ...]) -> str | None:
    for name in names:
        if not name:
            continue
        path = shutil.which(name)
        if path:
            return path
    return None


def find_make(env: dict[str, str] | None = None) -> str:
    """Locate GNU Make, preferring MDFIXER_MAKE for nonstandard hosts."""
    values = os.environ if env is None else env
    configured = values.get("MDFIXER_MAKE", "")
    names = (configured, "make", "mingw32-make", "gmake")
    executable = _find(names)
    if executable is None:
        raise RuntimeError(
            "GNU Make not found; install make or set MDFIXER_MAKE to its executable"
        )
    return executable


_SAFE_ENV = {
    "PATH", "HOME", "TMPDIR", "TMP", "TEMP", "SHELL", "LANG",
    "CC", "CFLAGS", "CPPFLAGS", "LDFLAGS", "MAKE", "MAKEFLAGS", "MFLAGS",
    "MDFIXER_MAKE", "SystemRoot", "COMSPEC", "PATHEXT", "WINDIR",
    "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
}


def safe_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Return only build-related variables, never service secrets."""
    values = dict(os.environ if env is None else env)
    return {
        key: value for key, value in values.items()
        if key in _SAFE_ENV or key.startswith("LC_")
    }


def _make_env(env: dict[str, str] | None) -> dict[str, str]:
    values = safe_env(env)
    # A SHELL environment variable makes mingw32-make bypass the command-line
    # override below; keep the environment free of it.
    if os.name == "nt":
        values.pop("SHELL", None)
    return values


def run_make(
    project_root: Path | str,
    *args: str,
    timeout: int = 120,
    env: dict[str, str] | None = None,
) -> CommandResult:
    root = Path(project_root).resolve()
    values = _make_env(env)
    try:
        make = find_make(values)
    except RuntimeError as exc:
        command = ("make", *args)
        return CommandResult(False, None, command, error=str(exc))
    command = (make, *args)
    if os.name == "nt" and _find(("sh",)):
        # mingw32-make defaults to CreateProcess; the command-line variable is
        # what makes it invoke Git Bash's sh for POSIX recipes such as `rm -f`.
        command = (make, "SHELL=sh", *args)
    try:
        result = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=values,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(False, None, command, error=f"timed out after {timeout}s")
    except OSError as exc:
        return CommandResult(False, None, command, error=str(exc))
    return CommandResult(
        result.returncode == 0,
        result.returncode,
        command,
        result.stdout,
        result.stderr,
    )


def clean_build(
    project_root: Path | str,
    target: str = "app",
    *,
    clean_target: str = "clean",
    timeout: int = 120,
    env: dict[str, str] | None = None,
) -> BuildResult:
    clean = run_make(project_root, clean_target, timeout=timeout, env=env)
    build = (
        run_make(project_root, target, timeout=timeout, env=env)
        if clean.ok
        else CommandResult(False, None, ("make", target), error="clean target failed")
    )
    return BuildResult(clean.ok and build.ok, clean, build)


def run_program(
    project_root: Path | str,
    program: str = "app",
    expected_output: str = "1",
    *,
    timeout: int = 30,
    env: dict[str, str] | None = None,
) -> ProgramResult:
    root = Path(project_root).resolve()
    candidates = [root / program, root / f"{program}.exe"]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        return ProgramResult(
            False, None, "", str(expected_output), program,
            error="program executable not found",
        )
    try:
        result = subprocess.run(
            [str(path)],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=_make_env(env),
        )
    except subprocess.TimeoutExpired:
        return ProgramResult(
            False, None, "", str(expected_output), program, str(path),
            error=f"timed out after {timeout}s",
        )
    except OSError as exc:
        return ProgramResult(
            False, None, "", str(expected_output), program, str(path), error=str(exc)
        )
    observed = result.stdout.strip()
    expected = str(expected_output).strip()
    ok = result.returncode == 0 and observed == expected
    error = None
    if result.returncode != 0:
        error = (result.stderr or result.stdout).strip() or "program failed"
    elif observed != expected:
        error = f"expected {expected!r}, observed {observed!r}"
    return ProgramResult(
        ok, result.returncode, result.stdout, expected, program, str(path), error
    )
