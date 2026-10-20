#!/usr/bin/env python3
"""Environment self-check for the reproducible E4 setup.

Runs on the local client and inspects the runtime server described by
``.env``. Local checks cover the repository, Git identity, client tools
and the ``.env`` file; server checks cover the Docker daemon, the Docker
socket and the server disk.
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
TIMEOUT_S = 15.0
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOCKET = "/var/run/docker.sock"
LOCAL_TOOLS = ("git", "make")
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


def _resolve_tool(name: str) -> str | None:
    if "/" in name:
        path = Path(name).expanduser()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    return shutil.which(name)


def _tool(name: str, repo: Path) -> dict[str, Any]:
    path = _resolve_tool(name)
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


def _docker_server(docker: str, repo: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "version": None,
        "os": None,
        "arch": None,
        "cpus": None,
        "memory_bytes": None,
        "root_dir": None,
    }
    code, out, err = _run([docker, "version", "--format", "{{.Server.Version}}"], cwd=repo)
    if code != 0 or not out:
        info["error"] = _first_line(err) or f"exit code {code}"
        return info
    info["version"] = out

    code, out, _ = _run([docker, "version", "--format", "{{.Server.Os}}/{{.Server.Arch}}"], cwd=repo)
    if code == 0 and "/" in out:
        info["os"], info["arch"] = out.split("/", 1)

    code, out, _ = _run(
        [docker, "info", "--format", "{{.NCPU}}|{{.MemTotal}}|{{.DockerRootDir}}"],
        cwd=repo,
    )
    if code == 0 and out.count("|") == 2:
        cpus, memory, root = out.split("|")
        try:
            info["cpus"] = int(cpus)
            info["memory_bytes"] = int(memory)
        except ValueError:
            pass
        info["root_dir"] = root or None
    return info


def _ssh_probe(ssh: str, repo: Path, sock: str) -> dict[str, Any]:
    script = (
        'echo "hostname=$(hostname)"; '
        'echo "system=$(uname -s)"; '
        'echo "release=$(uname -r)"; '
        'echo "machine=$(uname -m)"; '
        f'if [ -S {sock} ]; then echo "socket=present"; '
        f'echo "gid=$(stat -c %g {sock})"; '
        'else echo "socket=missing"; echo "gid="; fi'
    )
    info: dict[str, Any] = {
        "hostname": None,
        "system": None,
        "release": None,
        "machine": None,
        "socket_path": sock,
        "socket_exists": None,
        "socket_gid": None,
    }
    code, out, err = _run([ssh, "--shell", script], cwd=repo)
    if code != 0:
        info["error"] = _first_line(err) or f"exit code {code}"
        return info
    values: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    info["hostname"] = values.get("hostname") or None
    info["system"] = values.get("system") or None
    info["release"] = values.get("release") or None
    info["machine"] = values.get("machine") or None
    info["socket_exists"] = values.get("socket") == "present"
    gid = values.get("gid", "")
    info["socket_gid"] = int(gid) if gid.isdigit() else None
    return info


def _ssh_disk(ssh: str, repo: Path, path: str) -> dict[str, Any]:
    code, out, err = _run([ssh, "--shell", f"df -Pk '{path}' | tail -1"], cwd=repo)
    if code != 0 or not out:
        return {"path": path, "error": _first_line(err) or f"exit code {code}"}
    fields = out.split()
    if len(fields) < 6:
        return {"path": path, "error": f"unexpected df output: {out}"}
    try:
        total_kb, free_kb = int(fields[1]), int(fields[3])
    except ValueError:
        return {"path": path, "error": f"unexpected df output: {out}"}
    return {
        "path": fields[5],
        "free_gb": round(free_kb / (1024**2), 2),
        "total_gb": round(total_kb / (1024**2), 2),
    }


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


def _local_disk(repo: Path) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(repo)
    except OSError as exc:
        return {"error": str(exc)}
    return {
        "path": str(repo),
        "free_gb": round(usage.free / (1024**3), 2),
        "total_gb": round(usage.total / (1024**3), 2),
    }


def collect(
    repo: Path,
    *,
    docker: str = "docker",
    ssh: str | None = None,
    expected_gid: str | None = None,
    socket_path: str = DEFAULT_SOCKET,
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    local: dict[str, Any] = {}

    local["host"] = {
        "hostname": socket.gethostname(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "python_executable": sys.executable,
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
    local["repository"] = repo_info

    name = _git_value(repo, "user.name")
    email = _git_value(repo, "user.email")
    student_id = os.environ.get("STUDENT_ID")
    local["identity"] = {
        "name": name,
        "email": email,
        "student_id_env": student_id,
    }
    if not name or not email:
        add("git-identity", "fail", "user.name or user.email is not configured")
    else:
        detail = f"{name} <{email}>"
        if student_id and name != student_id:
            add("git-identity", "warn", f"{detail}; STUDENT_ID={student_id} differs from user.name")
        elif not re.fullmatch(r"\d{6,}", name):
            add("git-identity", "warn", f"{detail}; expected the student ID as user.name")
        else:
            add("git-identity", "ok", detail)

    tools: dict[str, Any] = {}
    for tool_name in LOCAL_TOOLS:
        tool = _tool(tool_name, repo)
        tools[tool_name] = tool
        if not tool["path"]:
            add(f"tool-{tool_name}", "fail", "not found on PATH")
        else:
            add(
                f"tool-{tool_name}",
                "ok",
                f"{tool['path']} ({tool['version'] or tool['raw'] or 'version unknown'})",
            )

    docker_tool = _tool(docker, repo)
    tools["docker"] = docker_tool
    if not docker_tool["path"]:
        add("tool-docker", "fail", f"not found: {docker}")
    else:
        add("tool-docker", "ok", f"{docker_tool['path']} ({docker_tool['raw'] or 'ok'})")

    if ssh:
        ssh_path = _resolve_tool(ssh)
        tools["ssh"] = {"path": ssh_path}
        if not ssh_path:
            add("tool-ssh", "fail", f"not found: {ssh}")
        else:
            add("tool-ssh", "ok", ssh_path)
        sshpass = shutil.which("sshpass")
        tools["sshpass"] = {"path": sshpass}
        if not sshpass:
            add("tool-sshpass", "fail", "sshpass is required by the remote wrapper")
        else:
            add("tool-sshpass", "ok", sshpass)
    local["tools"] = tools

    env_file = _env_file(repo)
    local["env_file"] = env_file
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

    local["disk"] = _local_disk(repo)
    if "free_gb" in local["disk"]:
        status = "warn" if local["disk"]["free_gb"] < 5 else "ok"
        add("local-disk", status, f"{local['disk']['free_gb']} GiB free at {repo}")
    else:
        add("local-disk", "warn", local["disk"].get("error", "disk usage unavailable"))

    server: dict[str, Any] = {
        "docker_command": docker,
        "ssh_command": ssh,
    }
    docker_server = _docker_server(docker, repo)
    server["docker"] = docker_server
    if docker_server["version"]:
        add(
            "docker-server",
            "ok",
            f"{docker_server['version']} ({docker_server['os'] or '?'}/{docker_server['arch'] or '?'})",
        )
    else:
        add("docker-server", "fail", docker_server.get("error") or "Docker server is unreachable")
    if docker_server["cpus"]:
        mem_gb = round((docker_server["memory_bytes"] or 0) / (1024**3), 2)
        add("server-resources", "ok", f"{docker_server['cpus']} cpus, {mem_gb} GiB memory")

    if ssh:
        probe = _ssh_probe(ssh, repo, socket_path)
        server["host"] = {
            "hostname": probe["hostname"],
            "system": probe["system"],
            "release": probe["release"],
            "machine": probe["machine"],
        }
        server["socket"] = {
            "path": probe["socket_path"],
            "exists": probe["socket_exists"],
            "gid": probe["socket_gid"],
            "expected_gid": expected_gid,
        }
        if probe.get("error"):
            add("server-host", "fail", probe["error"])
        else:
            add(
                "server-host",
                "ok",
                f"{probe['hostname']} ({probe['system']} {probe['release']} {probe['machine']})",
            )
        if probe["socket_exists"] is None:
            add("docker-socket", "fail", probe.get("error") or "socket state unknown")
        elif not probe["socket_exists"]:
            add("docker-socket", "fail", f"missing: {socket_path}")
        elif expected_gid and probe["socket_gid"] is not None and str(probe["socket_gid"]) != str(expected_gid):
            add(
                "docker-socket",
                "fail",
                f"DOCKER_GID={expected_gid} does not match server socket gid {probe['socket_gid']}",
            )
        else:
            add("docker-socket", "ok", f"{socket_path} (gid {probe['socket_gid']})")

        root_dir = docker_server.get("root_dir") or "/"
        disk = _ssh_disk(ssh, repo, root_dir)
        server["disk"] = disk
        if "free_gb" in disk:
            status = "warn" if disk["free_gb"] < 5 else "ok"
            add("server-disk", status, f"{disk['free_gb']} GiB free at {disk['path']}")
        else:
            add("server-disk", "warn", disk.get("error", "disk usage unavailable"))
    else:
        add("server-host", "skip", "--ssh not provided")
        add("docker-socket", "skip", "--ssh not provided")
        add("server-disk", "skip", "--ssh not provided")

    ok = not any(check["status"] == "fail" for check in checks)
    return {
        "schema_version": SCHEMA_VERSION,
        "service": SERVICE,
        "generated_at": _now_iso(),
        "ok": ok,
        "mode": "remote" if ssh else "local",
        "local": local,
        "server": server,
        "checks": checks,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="doctor",
        description="Check the local client and the remote Docker runtime.",
    )
    parser.add_argument(
        "--repo",
        default=str(REPO_ROOT),
        help=f"repository root (default: {REPO_ROOT})",
    )
    parser.add_argument(
        "--docker",
        default="docker",
        help="docker command or wrapper (default: docker)",
    )
    parser.add_argument(
        "--ssh",
        default=None,
        help="remote command wrapper for server checks (e.g. scripts/remote.sh)",
    )
    parser.add_argument(
        "--docker-gid",
        default=os.environ.get("DOCKER_GID"),
        help="expected server docker.sock gid (default: $DOCKER_GID)",
    )
    parser.add_argument(
        "--socket",
        default=DEFAULT_SOCKET,
        help=f"server docker socket path (default: {DEFAULT_SOCKET})",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="write the JSON report to this path; use '-' or omit to print it",
    )
    args = parser.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    payload = collect(
        repo,
        docker=args.docker,
        ssh=args.ssh,
        expected_gid=args.docker_gid,
        socket_path=args.socket,
    )
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
