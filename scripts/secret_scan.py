#!/usr/bin/env python3
"""Secret scan for the reproducible E4 environment.

Checks the three places where a key can leak (E4 slide 15):

1. ``workspace``   - tracked files plus untracked files Git does not ignore;
                     also guards that ``.env`` itself is neither tracked nor
                     un-ignored.
2. ``git-history`` - every added/removed patch line of ``git log --all -p``.
                     Deleting a file does not remove the key from history.
3. ``image``       - ``docker history --no-trunc`` of the built image. Every
                     build command of every layer stays readable.

Matches are never printed verbatim: only the first four characters, the
length and a SHA-256 fingerprint are reported, so the log itself is safe to
archive.

Exit status: 0 = pass, 1 = a secret was found, 2 = the scan could not run.

用法
    python3 scripts/secret_scan.py
    python3 scripts/secret_scan.py --image e4-draft:241250022 \
        --output work/<时间>/secret-scan.txt
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence

SCHEMA_VERSION = "1.0"
SERVICE = "draft"
REPO_ROOT = Path(__file__).resolve().parents[1]
REDACT_PREFIX = 4
MAX_SCAN_BYTES = 2 * 1024 * 1024
DEFAULT_TIMEOUT_S = 60.0
REPORT_FINDING_LIMIT = 200

# 不参与工作区扫描：Git 自身、构建产物、依赖、缓存
SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        ".idea",
        ".pytest_cache",
        "__pycache__",
        "evidence",
        "node_modules",
        "work",
    }
)

# 不参与工作区扫描的扩展名：密钥不会以可 grep 的文本形式出现在这里
SKIP_SUFFIXES = frozenset(
    {
        ".doc",
        ".docx",
        ".gif",
        ".gz",
        ".ico",
        ".jpeg",
        ".jpg",
        ".pdf",
        ".png",
        ".pptx",
        ".pyc",
        ".so",
        ".tar",
        ".webp",
        ".whl",
        ".zip",
    }
)

# .env 是允许存放密钥的文件；这些后缀只是占位样例
ENV_FILE_RE = re.compile(r"(^|/)\.env(\..+)?$")
ENV_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist")


@dataclass(frozen=True)
class Pattern:
    """One secret signature: the name shown in the report and its regex."""

    name: str
    regex: re.Pattern[str]
    note: str


PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "private-key-block",
        re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
        "私钥块",
    ),
    Pattern(
        "openai-style-key",
        re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
        "OpenAI 风格 sk- 密钥",
    ),
    Pattern(
        "github-token",
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
        "GitHub token",
    ),
    Pattern(
        "aws-access-key-id",
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        "AWS access key id",
    ),
    Pattern(
        "slack-token",
        re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
        "Slack token",
    ),
    Pattern(
        "named-secret-assignment",
        re.compile(
            r"""(?ix)
            (?:api[_-]?key|access[_-]?key|secret[_-]?key|auth[_-]?token
               |access[_-]?token|client[_-]?secret|password|passwd|secret)
            \s*[:=]\s*
            ["']?(?P<value>[A-Za-z0-9_\-./+=]{16,})["']?
            """
        ),
        "具名变量被赋值为长随机串",
    ),
)

# 允许清单：命中不判失败，但逐条在报告里列出原因，便于复核
ALLOWLIST: tuple[tuple[str, str, str], ...] = ()

# 结构性问题（不是密钥本身），报告时不做截断
STRUCTURAL_PATTERNS = frozenset({"tracked-env-file", "env-not-ignored"})


@dataclass(frozen=True)
class Finding:
    surface: str
    location: str
    pattern: str
    value: str

    @property
    def redact(self) -> bool:
        return self.pattern not in STRUCTURAL_PATTERNS

    @property
    def shown(self) -> str:
        """只暴露前 4 位，其余以长度代替；不是密钥的值原样显示。"""
        if not self.redact:
            return self.value
        return f"{self.value[:REDACT_PREFIX]}…({len(self.value)} chars)"

    @property
    def fingerprint(self) -> str:
        digest = hashlib.sha256(self.value.encode("utf-8", "surrogateescape")).hexdigest()
        return f"sha256:{digest[:12]}"

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.surface, self.location, self.fingerprint)

    def to_payload(self) -> dict[str, str]:
        return {
            "surface": self.surface,
            "pattern": self.pattern,
            "location": self.location,
            "match": self.shown,
            "fingerprint": self.fingerprint,
        }

    def to_line(self) -> str:
        return (
            f"finding: pattern={self.pattern} location={self.location} "
            f"match={self.shown} fingerprint={self.fingerprint}"
        )


@dataclass
class Surface:
    """One scan surface: what was scanned and what it turned up."""

    name: str
    status: str = "ok"  # ok | skipped | error
    detail: str = ""
    scanned: int = 0
    findings: list[Finding] = field(default_factory=list)
    allowlisted: list[tuple[Finding, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tail(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            list(command),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError:
        return 127, "", f"executable not found: {command[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout:g}s"
    except OSError as exc:
        return 126, "", str(exc)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _git(
    repo: Path,
    *args: str,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> tuple[int, str, str]:
    return _run(["git", *args], cwd=repo, timeout=timeout)


def _allowlist_reason(path: str, pattern: str) -> str | None:
    for glob, name, reason in ALLOWLIST:
        if name == pattern and Path(path).match(glob):
            return reason
    return None


def scan_line(text: str, *, surface: str, location: str) -> Iterator[Finding]:
    """Yield the findings of one line; one value is reported under one pattern.

    模式按“越具体越靠前”排列，因此同一个值只按最具体的模式报一次。
    """
    seen: set[str] = set()
    for pattern in PATTERNS:
        for match in pattern.regex.finditer(text):
            value = match.groupdict().get("value") or match.group(0)
            value = value.strip().strip("\"'")
            if len(value) < 16:
                continue
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            yield Finding(
                surface=surface,
                location=location,
                pattern=pattern.name,
                value=value,
            )


def _relative(path: Path, repo: Path) -> str | None:
    try:
        return path.resolve().relative_to(repo).as_posix()
    except (OSError, ValueError):
        return None


def _skip_reason(path: Path, repo: Path) -> str | None:
    relative = _relative(path, repo)
    if relative is None:
        return "outside repository"
    if any(part in SKIP_DIRS for part in relative.split("/")[:-1]):
        return "skipped directory"
    if path.suffix.lower() in SKIP_SUFFIXES:
        return "skipped file type"
    try:
        if path.stat().st_size > MAX_SCAN_BYTES:
            return f"larger than {MAX_SCAN_BYTES} bytes"
        with path.open("rb") as handle:
            if b"\x00" in handle.read(1024):
                return "binary"
    except OSError:
        return "unreadable"
    return None


def _list_paths(repo: Path, *args: str) -> list[Path]:
    code, out, err = _git(repo, "ls-files", "-z", *args)
    if code != 0:
        raise RuntimeError(f"git ls-files {' '.join(args)} failed: {_tail(err) or code}")
    return [repo / name for name in out.split("\0") if name]


def _scan_paths(surface: Surface, paths: Sequence[Path], repo: Path) -> None:
    for path in paths:
        relative = _relative(path, repo)
        if relative is None or _skip_reason(path, repo):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            surface.scanned += 1
            for finding in scan_line(line, surface=surface.name, location=f"{relative}:{lineno}"):
                reason = _allowlist_reason(relative, finding.pattern)
                if reason:
                    surface.allowlisted.append((finding, reason))
                else:
                    surface.findings.append(finding)


def scan_workspace(repo: Path) -> list[Surface]:
    """Tracked content plus untracked files Git does not ignore."""
    tracked = Surface("workspace")
    untracked = Surface("workspace-untracked")
    try:
        _scan_paths(tracked, _list_paths(repo), repo)
    except RuntimeError as exc:
        tracked.status = "error"
        tracked.detail = str(exc)
        untracked.status = "skipped"
        untracked.detail = "tracked scan failed"
        return [tracked, untracked]

    try:
        candidates = _list_paths(repo, "--others", "--exclude-standard")
    except RuntimeError as exc:
        untracked.status = "error"
        untracked.detail = str(exc)
        return [tracked, untracked]

    # 未跟踪的 .env 正是推荐做法，内容不扫
    _scan_paths(untracked, [path for path in candidates if not ENV_FILE_RE.search(path.as_posix())], repo)
    return [tracked, untracked]


def env_guard(repo: Path) -> Surface:
    """``git ls-files`` / ``git check-ignore`` guard dedicated to .env."""
    surface = Surface("env-guard")
    try:
        tracked = [path for path in _list_paths(repo) if ENV_FILE_RE.search(path.as_posix())]
    except RuntimeError as exc:
        surface.status = "error"
        surface.detail = str(exc)
        return surface

    tracked_relatives: set[str] = set()
    for path in tracked:
        relative = _relative(path, repo) or path.as_posix()
        tracked_relatives.add(relative)
        surface.scanned += 1
        if relative.endswith(ENV_TEMPLATE_SUFFIXES):
            surface.notes.append(f"{relative} 是占位样例，允许进入仓库")
            continue
        surface.findings.append(Finding(surface.name, relative, "tracked-env-file", relative))

    if (repo / ".env").exists():
        if ".env" in tracked_relatives:
            surface.notes.append(".env 已被 Git 跟踪：先 git rm --cached .env，再作废其中的 Key")
        else:
            surface.scanned += 1
            # --no-index：只看忽略规则本身，不受索引状态影响
            code, _, _ = _git(repo, "check-ignore", "-q", "--no-index", ".env")
            if code == 0:
                surface.notes.append(".env 存在且被 .gitignore 覆盖")
            else:
                surface.findings.append(
                    Finding(surface.name, ".gitignore", "env-not-ignored", ".env")
                )
    else:
        surface.notes.append(".env 尚未创建（复制 .env.example 后填入自己的值）")
    return surface


def _history_command(limit: int | None) -> list[str]:
    command = [
        "git",
        "-c",
        "core.pager=cat",
        "log",
        "--all",
        "--no-color",
        "--no-ext-diff",
        "--unified=0",
        "-p",
        "--format=commit %H",
    ]
    if limit is not None:
        command.append(f"--max-count={limit}")
    return command


def scan_history(repo: Path, *, limit: int | None = None) -> Surface:
    """Scan every added/removed patch line of ``git log --all -p``."""
    surface = Surface("git-history")
    try:
        proc = subprocess.Popen(
            _history_command(limit),
            cwd=str(repo),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        surface.status = "error"
        surface.detail = str(exc)
        return surface

    seen: set[tuple[str, str, str]] = set()
    commits = 0
    commit = ""
    path = ""
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            if line.startswith("commit "):
                commit = line.split(maxsplit=1)[1].strip() if " " in line else ""
                commits += 1
                continue
            if line.startswith("diff --git "):
                # 删除文件时没有 +++ b/ 行，路径只能从 diff --git 取
                parts = line.split(" b/", 1)
                path = parts[1].strip() if len(parts) == 2 else ""
                continue
            if line.startswith("+++ b/"):
                path = line[6:].strip()
                continue
            if not line.startswith(("+", "-")) or line.startswith(("+++", "---")):
                continue
            surface.scanned += 1
            location = f"{commit[:12]}:{path or '(unknown)'}"
            for finding in scan_line(line, surface=surface.name, location=location):
                if finding.key in seen:
                    continue
                seen.add(finding.key)
                reason = _allowlist_reason(path, finding.pattern)
                if reason:
                    surface.allowlisted.append((finding, reason))
                else:
                    surface.findings.append(finding)
    finally:
        stderr = proc.stderr.read() if proc.stderr else ""
        code = proc.wait()

    surface.notes.append(f"commits: {commits}")
    if code != 0:
        surface.status = "error"
        surface.detail = f"git log exited {code}: {_tail(stderr)}"
    return surface


def scan_image(image: str, *, repo: Path, docker: str = "docker") -> Surface:
    """Scan ``docker history --no-trunc`` of one image."""
    surface = Surface("image", detail=image)
    code, out, err = _run([docker, "image", "inspect", "--format", "{{.Id}}", image], cwd=repo)
    if code != 0:
        surface.status = "skipped"
        tail = _tail(err)
        surface.detail = f"{image} 不存在，跳过镜像层检查（先执行 make build）"
        if tail:
            surface.detail += f"：{tail}"
        return surface

    code, out, err = _run(
        [docker, "history", "--no-trunc", "--format", "{{.ID}} {{.CreatedBy}}", image],
        cwd=repo,
    )
    if code != 0:
        surface.status = "error"
        surface.detail = f"docker history failed: {_tail(err) or code}"
        return surface

    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        surface.scanned += 1
        layer = line.split(" ", 1)[0]
        location = f"{image}:{layer}"
        for finding in scan_line(line, surface=surface.name, location=location):
            reason = _allowlist_reason(location, finding.pattern)
            if reason:
                surface.allowlisted.append((finding, reason))
            else:
                surface.findings.append(finding)
    return surface


def _image_candidates(repo: Path, docker: str) -> list[str]:
    """Images worth scanning: the compose image and local ``e4-draft*`` tags."""
    images: list[str] = []
    student_id = os.environ.get("STUDENT_ID", "").strip()
    if student_id:
        images.append(f"e4-draft:{student_id}")
    code, out, _ = _run([docker, "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"], cwd=repo)
    if code == 0:
        images.extend(
            line.strip()
            for line in out.splitlines()
            if line.startswith(("e4-draft:", "e4-draft-"))
        )
    return list(dict.fromkeys(images))


def _scanner_sha(repo: Path) -> str:
    try:
        return hashlib.sha256((repo / "scripts" / "secret_scan.py").read_bytes()).hexdigest()
    except OSError:
        return "unknown"


def collect(
    repo: Path,
    *,
    images: Sequence[str] = (),
    docker: str = "docker",
    history_limit: int | None = None,
    include_history: bool = True,
    include_image: bool = True,
) -> tuple[dict, list[Surface]]:
    surfaces = scan_workspace(repo)
    surfaces.append(env_guard(repo))

    if include_history:
        surfaces.append(scan_history(repo, limit=history_limit))
    else:
        surfaces.append(Surface("git-history", status="skipped", detail="--no-history"))

    if not include_image:
        surfaces.append(Surface("image", status="skipped", detail="--no-image"))
    else:
        targets = list(dict.fromkeys(images)) or _image_candidates(repo, docker)
        if targets:
            surfaces.extend(scan_image(image, repo=repo, docker=docker) for image in targets)
        else:
            surfaces.append(
                Surface("image", status="skipped", detail="没有可检查的镜像（未构建，或 docker 不可用）")
            )

    findings = [finding for surface in surfaces for finding in surface.findings]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "service": SERVICE,
        "generated_at": _now_iso(),
        "repo": str(repo),
        "scanner_sha256": _scanner_sha(repo),
        "patterns": [pattern.name for pattern in PATTERNS],
        "ok": not findings,
        "totals": {
            "surfaces": len(surfaces),
            "findings": len(findings),
            "allowlisted": sum(len(surface.allowlisted) for surface in surfaces),
            "lines_scanned": sum(surface.scanned for surface in surfaces),
        },
        "surfaces": [
            {
                "name": surface.name,
                "status": surface.status,
                "detail": surface.detail,
                "lines_scanned": surface.scanned,
                "notes": surface.notes,
                "findings": [finding.to_payload() for finding in surface.findings],
                "allowlisted": [
                    {"reason": reason, **finding.to_payload()}
                    for finding, reason in surface.allowlisted
                ],
            }
            for surface in surfaces
        ],
        "findings": [finding.to_payload() for finding in findings],
    }
    return payload, surfaces


def render_text(payload: dict, surfaces: Sequence[Surface]) -> str:
    lines = [
        "# E4-B07 secret scan",
        f"generated_at_utc: {payload['generated_at']}",
        f"repo: {payload['repo']}",
        f"scanner_sha256: {payload['scanner_sha256']}",
        f"patterns: {', '.join(payload['patterns'])}",
        "",
        "命中只输出前 4 位与指纹；完整密钥不落盘。",
        "",
    ]
    for surface in surfaces:
        lines.append(f"## {surface.name}")
        lines.append(f"status: {surface.status}")
        if surface.detail:
            lines.append(f"detail: {surface.detail}")
        for note in surface.notes:
            lines.append(f"note: {note}")
        lines.append(f"lines_scanned: {surface.scanned}")
        lines.append(f"findings: {len(surface.findings)}")
        for finding in surface.findings[:REPORT_FINDING_LIMIT]:
            lines.append(finding.to_line())
        if len(surface.findings) > REPORT_FINDING_LIMIT:
            lines.append(
                f"finding-omitted: 还有 {len(surface.findings) - REPORT_FINDING_LIMIT} 条未列出"
            )
        lines.append(f"allowlisted: {len(surface.allowlisted)}")
        for finding, reason in surface.allowlisted:
            lines.append(
                f"allowlisted-finding: pattern={finding.pattern} "
                f"location={finding.location} reason={reason}"
            )
        lines.append("")

    totals = payload["totals"]
    verdict = "PASS" if payload["ok"] else "FAIL"
    lines.append(
        f"result: {verdict} ({totals['findings']} findings, "
        f"{totals['allowlisted']} allowlisted, {totals['lines_scanned']} lines scanned)"
    )
    if not payload["ok"]:
        lines.append("next: 先作废 Key，再清理提交历史与镜像；删除 commit 收不回已推送内容。")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="secret_scan",
        description="Scan workspace, Git history and image layers for leaked secrets.",
    )
    parser.add_argument(
        "--repo",
        default=str(REPO_ROOT),
        help=f"repository root (default: {REPO_ROOT})",
    )
    parser.add_argument(
        "--image",
        action="append",
        default=[],
        help="image to inspect via docker history; repeatable "
        "(default: compose image e4-draft:$STUDENT_ID plus local e4-draft* tags)",
    )
    parser.add_argument("--docker", default="docker", help="docker executable (default: docker)")
    parser.add_argument("--no-history", action="store_true", help="skip the Git history surface")
    parser.add_argument("--no-image", action="store_true", help="skip the image layer surface")
    parser.add_argument(
        "--history-limit",
        type=int,
        default=None,
        help="only inspect the newest N commits of each ref (default: all)",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="report format (default: text, suitable for secret-scan.txt)",
    )
    parser.add_argument(
        "--output",
        default="-",
        help="write the report here; '-' or omit to print to stdout",
    )
    args = parser.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    if not (repo / ".git").exists():
        print(f"secret_scan: not a Git repository: {repo}", file=sys.stderr)
        return 2

    payload, surfaces = collect(
        repo,
        images=args.image,
        docker=args.docker,
        history_limit=args.history_limit,
        include_history=not args.no_history,
        include_image=not args.no_image,
    )

    text = (
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if args.format == "json"
        else render_text(payload, surfaces)
    )

    if args.output and args.output != "-":
        target = Path(args.output).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        print(f"secret report written to {target}", file=sys.stderr)
    else:
        print(text, end="")

    totals = payload["totals"]
    print(
        f"secret_scan: {totals['surfaces']} surfaces, {totals['lines_scanned']} lines, "
        f"{totals['findings']} findings, {totals['allowlisted']} allowlisted",
        file=sys.stderr,
    )
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
