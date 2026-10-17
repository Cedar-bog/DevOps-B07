#!/usr/bin/env python3
"""Environment self-check for the reproducible E4 setup.

Collects host, repository, toolchain, Docker and environment-file state
into a JSON report. Required checks failing make the script exit nonzero.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

SCHEMA_VERSION = "1.0"
SERVICE = "draft"
TIMEOUT_S = 10.0
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOCKET = Path("/var/run/docker.sock")
REQUIRED_TOOLS = ("docker", "git", "make")
OPTIONAL_TOOLS = ("gcc", "strace")
ALL_TOOLS = REQUIRED_TOOLS + OPTIONAL_TOOLS
WINDOWS = os.name == "nt"
VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+)?")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _version(text: str) -> str | None:
    match = VERSION_RE.search(text)
    return match.group(0) if match else None


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: float = TIMEOUT_S,
) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            list(command),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return 127, "", f"executable not found: {command[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout:g}s"
    except OSError as exc:
        return 126, "", str(exc)
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def _git(repo: Path, *args: str) -> tuple[int, str, str]:
    return _run(["git", *args], cwd=repo)


def _git_value(repo: Path, key: str) -> str | None:
    code, out, _ = _git(repo, "config", "--get", key)
    return out if code == 0 and out else None


def _tool(name: str, repo: Path) -> dict[str, Any]:
    path = shutil.which(name)
    info: dict[str, Any] = {"path": path, "version": None, "raw": None}
    if not path:
        return info
    code, out, err = _run([path, "--version"], cwd=repo)
    text = out or err
    if text:
        info["raw"] = _first_line(text)
        info["version"] = _version(text)
    if code != 0 and not info["raw"]:
        info["error"] = _first_line(err) or f"exit code {code}"
    return info


def _compose_version(repo: Path, docker_path: str | None) -> dict[str, Any]:
    if not docker_path:
        return {"path": None, "version": None}
    code, out, err = _run([docker_path, "compose", "version", "--short"], cwd=repo)
    text = out or err
    return {
        "path": docker_path,
        "version": _version(text) if code == 0 else None,
        "raw": _first_line(text),
        "error": None if code == 0 else (_first_line(err) or f"exit code {code}"),
    }


def _docker_server(repo: Path, docker_path: str | None) -> dict[str, Any]:
    info: dict[str, Any] = {"version": None, "os": None, "arch": None}
    if not docker_path:
        info["error"] = "docker executable not found"
        return info
    code, out, err = _run(
        [docker_path, "version", "--format", "{{.Server.Version}}"],
        cwd=repo,
    )
    if code != 0 or not out:
        info["error"] = _first_line(err) or f"exit code {code}"
        return info
    info["version"] = out
    code, out, err = _run(
        [docker_path, "version", "--format", "{{.Server.Os}}/{{.Server.Arch}}"],
        cwd=repo,
    )
    if code == 0 and "/" in out:
        info["os"], info["arch"] = out.split("/", 1)
    return info


def _docker_socket(repo: Path) -> dict[str, Any]:
    host = os.environ.get("DOCKER_HOST", "")
    if host.startswith("unix://"):
        path = Path(host.removeprefix("unix://"))
    else:
        path = DEFAULT_SOCKET
    info: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "gid": None,
        "env_gid": os.environ.get("DOCKER_GID"),
        "gid_matches": None,
        "accessible": None,
        "in_group": None,
    }
    if WINDOWS:
        return info
    if not path.exists():
        return info
    try:
        info["gid"] = path.stat().st_gid
    except OSError as exc:
        info["error"] = str(exc)
        return info
    info["accessible"] = os.access(path, os.R_OK | os.W_OK)
    try:
        info["in_group"] = path.stat().st_gid in os.getgroups() or path.stat().st_gid == os.getegid()
    except OSError:
        info["in_group"] = None
    if info["env_gid"] is not None:
        info["gid_matches"] = str(info["gid"]) == str(info["env_gid"])
    return info


def _env_file(repo: Path) -> dict[str, Any]:
    path = repo / ".env"
    info: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "mode": None,
        "tracked": False,
        "ignored": False,
        "example_exists": (repo / ".env.example").exists(),
    }
    if path.exists() and not WINDOWS:
        try:
            info["mode"] = f"{stat.S_IMODE(path.stat().st_mode):04o}"
        except OSError as exc:
            info["error"] = str(exc)
    code, _, _ = _git(repo, "ls-files", "--error-unmatch", ".env")
    info["tracked"] = code == 0
    code, _, _ = _git(repo, "check-ignore", "-q", ".env")
    info["ignored"] = code == 0
    return info


def _disk(repo: Path) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(repo)
    except OSError as exc:
        return {"error": str(exc)}
    return {
        "path": str(repo),
        "free_gb": round(usage.free / (1024**3), 2),
        "total_gb": round(usage.total / (1024**3), 2),
    }


def collect(repo: Path) -> dict[str, Any]:
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    host = {
        "hostname": socket.gethostname(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "python_executable": sys.executable,
    }
    try:
        os_release = platform.freedesktop_os_release()
    except (AttributeError, OSError):
        os_release = {}
    if os_release:
        host["os_release"] = {
            key: os_release.get(key)
            for key in ("ID", "VERSION_ID", "PRETTY_NAME")
            if os_release.get(key)
        }

    code, out, err = _git(repo, "rev-parse", "--show-toplevel")
    in_repo = code == 0 and bool(out)
    repo_info: dict[str, Any] = {
        "root": out if in_repo else None,
        "commit": None,
        "branch": None,
        "dirty": None,
    }
    if in_repo:
        code, commit, _ = _git(repo, "rev-parse", "HEAD")
        repo_info["commit"] = commit if code == 0 else None
        code, branch, _ = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        repo_info["branch"] = branch if code == 0 else None
        code, status, _ = _git(repo, "status", "--porcelain")
        repo_info["dirty"] = bool(status) if code == 0 else None
        add("git-repository", "ok", repo_info["root"])
    else:
        add("git-repository", "fail", _first_line(err) or "not a Git repository")

    name = _git_value(repo, "user.name")
    email = _git_value(repo, "user.email")
    student_id = os.environ.get("STUDENT_ID")
    identity = {
        "name": name,
        "email": email,
        "student_id_env": student_id,
    }
    if not name or not email:
        add("git-identity", "fail", "user.name or user.email is not configured")
    else:
        detail = f"{name} <{email}>"
        if student_id and name != student_id:
            add(
                "git-identity",
                "warn",
                f"{detail}; STUDENT_ID={student_id} differs from user.name",
            )
        elif not re.fullmatch(r"\d{6,}", name):
            add("git-identity", "warn", f"{detail}; expected the student ID as user.name")
        else:
            add("git-identity", "ok", detail)

    tools: dict[str, Any] = {}
    for tool_name in ALL_TOOLS:
        tool = _tool(tool_name, repo)
        tools[tool_name] = tool
        if not tool["path"]:
            status = "fail" if tool_name in REQUIRED_TOOLS else "warn"
            add(f"tool-{tool_name}", status, "not found on PATH")
        else:
            add(
                f"tool-{tool_name}",
                "ok",
                f"{tool['path']} ({tool['version'] or tool['raw'] or 'version unknown'})",
            )

    docker_path = tools.get("docker", {}).get("path")
    compose = _compose_version(repo, docker_path)
    tools["docker-compose"] = compose
    if compose["version"]:
        add("tool-docker-compose", "ok", f"{compose['path']} ({compose['version']})")
    else:
        add("tool-docker-compose", "fail", compose.get("error") or "docker compose is unavailable")

    server = _docker_server(repo, docker_path)
    if server["version"]:
        add(
            "docker-server",
            "ok",
            f"{server['version']} ({server['os'] or '?'}/{server['arch'] or '?'})",
        )
    else:
        add("docker-server", "fail", server.get("error") or "Docker server is unreachable")

    docker_socket = _docker_socket(repo)
    if WINDOWS:
        add("docker-socket", "skip", "not applicable on Windows")
    elif not docker_socket["exists"]:
        add("docker-socket", "fail", f"missing: {docker_socket['path']}")
    elif not docker_socket["accessible"]:
        add(
            "docker-socket",
            "fail",
            f"not accessible: {docker_socket['path']} (gid {docker_socket['gid']})",
        )
    elif docker_socket["gid_matches"] is False:
        add(
            "docker-socket",
            "fail",
            f"DOCKER_GID={docker_socket['env_gid']} does not match socket gid "
            f"{docker_socket['gid']}",
        )
    elif docker_socket["env_gid"] is None:
        add(
            "docker-socket",
            "warn",
            f"accessible (gid {docker_socket['gid']}); DOCKER_GID is not set",
        )
    else:
        add(
            "docker-socket",
            "ok",
            f"{docker_socket['path']} (gid {docker_socket['gid']})",
        )

    env_file = _env_file(repo)
    if not env_file["example_exists"]:
        add("env-example", "fail", ".env.example is missing")
    else:
        add("env-example", "ok", ".env.example")
    if not env_file["exists"]:
        add("env-file", "fail", ".env is missing; copy .env.example to .env")
    else:
        add("env-file", "ok", env_file["path"])
        if env_file["mode"] and env_file["mode"] != "0600":
            add("env-file-mode", "fail", f"mode {env_file['mode']}, expected 0600")
        elif env_file["mode"]:
            add("env-file-mode", "ok", "0600")
        else:
            add("env-file-mode", "skip", "not applicable on Windows")
    if env_file["tracked"]:
        add("env-file-tracked", "fail", ".env is tracked by Git")
    else:
        add("env-file-tracked", "ok", ".env is not tracked")
    if not env_file["ignored"]:
        add("env-file-ignored", "warn", ".env is not covered by .gitignore")
    else:
        add("env-file-ignored", "ok", ".env is ignored")

    disk = _disk(repo)
    if "free_gb" in disk:
        status = "warn" if disk["free_gb"] < 5 else "ok"
        add("disk", status, f"{disk['free_gb']} GiB free at {disk['path']}")
    else:
        add("disk", "warn", disk.get("error", "disk usage unavailable"))

    ok = not any(check["status"] == "fail" for check in checks)
    return {
        "schema_version": SCHEMA_VERSION,
        "service": SERVICE,
        "generated_at": _now_iso(),
        "ok": ok,
        "host": host,
        "repository": repo_info,
        "identity": identity,
        "tools": tools,
        "docker": {"server": server, "socket": docker_socket},
        "env_file": env_file,
        "disk": disk,
        "checks": checks,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="doctor",
        description="Check the host, Git identity, toolchain and Docker setup.",
    )
    parser.add_argument(
        "--repo",
        default=str(REPO_ROOT),
        help=f"repository root (default: {REPO_ROOT})",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="write the JSON report to this path; use '-' or omit to print it",
    )
    args = parser.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    payload = collect(repo)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    if args.output and args.output != "-":
        target = Path(args.output).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"environment report written to {target}", file=sys.stderr)
    else:
        print(text, end="")

    failures = [check for check in payload["checks"] if check["status"] == "fail"]
    warnings = [check for check in payload["checks"] if check["status"] == "warn"]
    print(
        f"doctor: {len(payload['checks']) - len(failures) - len(warnings)} ok, "
        f"{len(warnings)} warnings, {len(failures)} failed",
        file=sys.stderr,
    )
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
