#!/usr/bin/env python3
"""Run the E11 B-track integration against the deployed services."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REMOTE = ROOT / "scripts" / "remote" / "remote.sh"
FIXTURES = ROOT / "fixtures" / "e11"
SAMPLE_PREPARE = FIXTURES / "prepare_sample.py"
SAMPLE_MANIFEST = FIXTURES / "sample.json"
CONTAINER = "b07-e11-sample"
DRAFT_PORT = 8080
MDFIXER_PORT = 8081
DRAFT_NETWORK = "b07-draft_default"
MDFIXER_NETWORK = "b07-mdfixer_default"
DRAFT_DATA = "/opt/devops-b07/draft/data"
MDFIXER_DATA = "/opt/devops-b07/mdfixer/data"
LLM_KEYS = ("LLM_API_URL", "LLM_API_KEY", "LLM_MODEL")


class E11Error(RuntimeError):
    pass


def now_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def run_local(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise E11Error(result.stderr.strip() or result.stdout.strip() or "local command failed")
    return result.stdout


def remote(command: str, *, stdin: bytes | str | None = None, binary: bool = False) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [str(REMOTE), "--shell", command],
        input=stdin,
        capture_output=True,
        text=not binary,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr if not binary else result.stderr.decode("utf-8", "replace")
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        raise E11Error(detail.strip() or "remote command failed")
    return result


def remote_text(command: str, *, stdin: bytes | str | None = None) -> str:
    return remote(command, stdin=stdin).stdout


def remote_json(command: str) -> Any:
    return json.loads(remote_text(command))


def write_remote(path: str, data: bytes) -> None:
    remote(
        f"mkdir -p {shlex.quote(str(Path(path).parent))} && cat > {shlex.quote(path)} && chmod 600 {shlex.quote(path)}",
        stdin=data.decode("utf-8"),
    )


def tar_bytes(path: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for item in sorted(path.rglob("*")):
            tar.add(item, arcname=item.relative_to(path))
    return buffer.getvalue()


def upload_tree(source: Path, destination: str) -> None:
    remote(
        f"rm -rf {shlex.quote(destination)} && mkdir -p {shlex.quote(destination)} && tar -C {shlex.quote(destination)} -xf -",
        stdin=tar_bytes(source),
        binary=True,
    )


def upload_repo(source: Path, name: str, remote_base: str) -> None:
    repo = f"{remote_base}/{name}"
    upload_tree(source, repo)
    remote(
        "chown -R root:root {repo} && cd {base} && rm -rf {name}.git && "
        "git clone --bare {name} {name}.git >/dev/null 2>&1 && "
        "git --git-dir={name}.git update-server-info".format(
            repo=shlex.quote(repo), base=shlex.quote(remote_base), name=shlex.quote(name)
        )
    )


def build_sample(workspace: Path) -> dict[str, Any]:
    repo = workspace / "sample"
    result = subprocess.run(
        [sys.executable, str(SAMPLE_PREPARE), "--output", str(repo), "--manifest", str(SAMPLE_MANIFEST)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise E11Error(result.stderr.strip() or "prepare_sample failed")
    return json.loads(SAMPLE_MANIFEST.read_text(encoding="utf-8"))


def build_macro(workspace: Path, run_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    repo = workspace / "macro"
    shutil.copytree(FIXTURES / "styles" / "macro", repo)
    run_local("git", "init", "-q", cwd=repo)
    run_local("git", "config", "core.autocrlf", "false", cwd=repo)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "B07 E11",
        "GIT_AUTHOR_EMAIL": "b07-e11@example.invalid",
        "GIT_COMMITTER_NAME": "B07 E11",
        "GIT_COMMITTER_EMAIL": "b07-e11@example.invalid",
        "GIT_AUTHOR_DATE": "2026-11-26T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-11-26T00:00:00Z",
    }
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "macro"], cwd=repo, check=True, env=env)
    commit = run_local("git", "rev-parse", "HEAD", cwd=repo).strip()
    text = (repo / "Makefile").read_text(encoding="utf-8")
    line = next(index for index, value in enumerate(text.splitlines(), 1) if value.startswith("main.o:"))
    declaration = text.splitlines()[line - 1].strip()
    report = {
        "schema_version": "1.0",
        "repository": {"url": "http://b07-e11-sample:8000/macro.git", "commit": commit},
        "configuration_id": "e11-macro",
        "producer_job_id": "job-e11-macro",
        "findings": [
            {
                "finding_id": "MISSING-main-o-config-h",
                "type": "MISSING",
                "target": "main.o",
                "dependency": "config.h",
                "commit": commit,
                "position": {"file": "Makefile", "line": line, "declaration": declaration},
                "detector": "INSTRUCTOR_ORACLE",
            }
        ],
    }
    raw = (json.dumps(report, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode()
    digest = hashlib.sha256(raw).hexdigest()
    request = {
        "schema_version": "1.0",
        "trace_id": "trace-e11-macro",
        "job_type": "REPAIR",
        "input": {
            "repository": {"url": "http://b07-e11-sample:8000/macro.git", "commit": commit},
            "commit": commit,
            "md_report_uri": "artifact://pair07/e11-macro/report.json",
            "md_report_sha256": digest,
            "makefile": "Makefile",
            "env": {"build": "make hello", "verify": "./hello", "verify_expected_output": "1"},
            "recheck": {
                "header": "config.h",
                "macro": "VALUE",
                "old_value": "1",
                "new_value": "2",
                "target": "hello",
                "program": "./hello",
                "expected_output": "2",
                "timeout_seconds": 120,
            },
            "time_limit": 300,
            "idempotency_key": f"e11-macro-{run_id}",
        },
    }
    return repo, report, request


def start_server(remote_base: str, image: str) -> None:
    remote(f"docker rm -f {shlex.quote(CONTAINER)} >/dev/null 2>&1 || true")
    remote(
        "docker run -d --rm --name {name} --network {network} --user 0:0 "
        "-v {base}:/work:ro --entrypoint python {image} -m http.server 8000 --directory /work >/dev/null".format(
            name=shlex.quote(CONTAINER),
            network=shlex.quote(DRAFT_NETWORK),
            base=shlex.quote(remote_base),
            image=shlex.quote(image),
        )
    )
    remote(f"docker network connect {shlex.quote(MDFIXER_NETWORK)} {shlex.quote(CONTAINER)} >/dev/null 2>&1 || true")
    for service in ("b07-draft", "b07-mdfixer"):
        code = (
            "import urllib.request;"
            "print(urllib.request.urlopen('http://{name}:8000/repo.git/info/refs', timeout=10).status)"
        ).format(name=CONTAINER)
        remote_text(f"docker exec {shlex.quote(service)} python -c {shlex.quote(code)}")


def submit(remote_base: str, body: dict[str, Any], port: int, *, headers: bool = False) -> tuple[int, dict[str, Any]]:
    request_path = f"{remote_base}/request.json"
    write_remote(request_path, (json.dumps(body, ensure_ascii=False) + "\n").encode())
    config = ""
    if headers:
        config = f"-K {shlex.quote(remote_base + '/llm.conf')}"
    status = remote_text(
        "curl -sS -o {out} -w '%{{http_code}}' {config} -H 'Content-Type: application/json' "
        "--data-binary @{body} http://127.0.0.1:{port}/v1/{kind}-jobs".format(
            out=shlex.quote(remote_base + "/response.json"),
            config=config,
            body=shlex.quote(request_path),
            port=port,
            kind="dockerfile" if port == DRAFT_PORT else "repair",
        )
    ).strip()
    payload = remote_json(f"cat {shlex.quote(remote_base + '/response.json')}")
    return int(status), payload


def poll(remote_base: str, job_id: str, port: int, timeout: int = 900) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = remote_json(f"curl -fsS http://127.0.0.1:{port}/v1/jobs/{shlex.quote(job_id)}")
        if job.get("status") in {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"}:
            return job
        time.sleep(2)
    raise E11Error(f"job {job_id} did not finish")


def copy_logs(service: str, job_id: str, destination: Path) -> None:
    data_dir = DRAFT_DATA if service == "draft" else MDFIXER_DATA
    path = f"{data_dir}/jobs/{job_id}/logs"
    listing = remote_text(f"find {shlex.quote(path)} -maxdepth 1 -type f -printf '%f\n' 2>/dev/null || true")
    if not listing.strip():
        return
    destination = destination / "logs"
    destination.mkdir(parents=True, exist_ok=True)
    for name in listing.splitlines():
        if not name or "/" in name:
            continue
        data = remote(f"cat {shlex.quote(path + '/' + name)}", binary=True).stdout
        (destination / name).write_bytes(data)


def save_job(directory: Path, name: str, job: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.json").write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare_faults(base_report: dict[str, Any], base_request: dict[str, Any]) -> list[tuple[str, dict[str, Any], dict[str, Any], str]]:
    import copy

    cases: list[tuple[str, dict[str, Any], dict[str, Any], str]] = []
    for name, code, mutate in (
        ("schema", "REPAIR_6001", lambda report: report.update(schema_version="2.0")),
        ("stale", "REPAIR_6002", lambda report: (
            report.update({"repository": {**report["repository"], "commit": "b" * 40}}),
            [finding.update(commit="b" * 40) for finding in report["findings"]],
        )),
    ):
        report = copy.deepcopy(base_report)
        mutate(report)
        raw = (json.dumps(report, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode()
        request = copy.deepcopy(base_request)
        request["input"]["md_report_uri"] = f"artifact://pair07/e11-target/{name}.json"
        request["input"]["md_report_sha256"] = hashlib.sha256(raw).hexdigest()
        request["input"]["idempotency_key"] = f"e11-fault-{name}"
        cases.append((name, report, request, code))

    report = copy.deepcopy(base_report)
    raw = (json.dumps(report, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode()
    request = copy.deepcopy(base_request)
    request["input"]["md_report_uri"] = "artifact://pair07/e11-target/type.txt"
    request["input"]["md_report_sha256"] = hashlib.sha256(raw).hexdigest()
    request["input"]["idempotency_key"] = "e11-fault-type"
    cases.append(("type", report, request, "REPAIR_6003", ".txt"))

    request = copy.deepcopy(base_request)
    request["input"]["md_report_sha256"] = "0" * 64
    request["input"]["idempotency_key"] = "e11-fault-hash"
    cases.append(("hash", base_report, request, "REPAIR_6004"))
    return cases


def run(workspace: Path, out: Path, env: dict[str, str]) -> dict[str, Any]:
    if out.exists():
        raise E11Error(f"evidence directory already exists: {out}")
    out.mkdir(parents=True)
    run_id = out.name
    remote_base = f"/tmp/b07-e11-{run_id.replace(':', '').replace('-', '')}"
    report = json.loads((FIXTURES / "fixed-md-report.json").read_text(encoding="utf-8"))
    report_raw = (FIXTURES / "fixed-md-report.json").read_bytes()
    draft_request = json.loads((FIXTURES / "draft-request.json").read_text(encoding="utf-8"))
    repair_request = json.loads((FIXTURES / "repair-request.json").read_text(encoding="utf-8"))
    draft_request["input"]["idempotency_key"] = f"e11-draft-{run_id}"
    repair_request["input"]["idempotency_key"] = f"e11-repair-{run_id}"
    manifest = build_sample(workspace)
    macro_repo, macro_report, macro_request = build_macro(workspace, run_id)
    image = remote_text("cat /opt/devops-b07/mdfixer/current_tag").strip()
    if not image:
        raise E11Error("cannot read deployed MDFixer tag")

    jobs: list[tuple[str, str]] = []
    draft_job_id = ""
    summary: dict[str, Any] = {"run_id": run_id, "sample": manifest, "checks": {}, "faults": {}}
    try:
        remote(f"rm -rf {shlex.quote(remote_base)} && mkdir -p {shlex.quote(remote_base)}")
        upload_repo(workspace / "sample", "repo", remote_base)
        upload_repo(macro_repo, "macro", remote_base)
        start_server(remote_base, f"b07-mdfixer:{image}")
        write_remote(f"{remote_base}/llm.conf", (
            "header = \"X-LLM-Api-Url: {url}\"\n"
            "header = \"X-LLM-Api-Key: {key}\"\n"
            "header = \"X-LLM-Model: {model}\"\n"
        ).format(url=env["LLM_API_URL"], key=env["LLM_API_KEY"], model=env["LLM_MODEL"]).encode())
        write_remote(f"{MDFIXER_DATA}/artifacts/pair07/e11-target/report.json", report_raw)
        write_remote(f"{MDFIXER_DATA}/artifacts/pair07/e11-macro/report.json", (
            json.dumps(macro_report, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        ).encode())

        status, submitted = submit(remote_base, draft_request, DRAFT_PORT, headers=True)
        if status != 202:
            raise E11Error(f"DRAFT submit returned {status}: {submitted}")
        draft_job_id = submitted["job_id"]
        jobs.append(("draft", draft_job_id))
        draft_job = poll(remote_base, draft_job_id, DRAFT_PORT)
        save_job(out / "draft", "request", draft_request)
        save_job(out / "draft", "job", draft_job)
        copy_logs("draft", draft_job_id, out / "draft")
        output = draft_job.get("output") or {}
        rounds = output.get("rounds") or []
        if draft_job.get("status") != "SUCCEEDED" or len(rounds) < 2:
            raise E11Error(f"DRAFT did not complete two rounds: {draft_job.get('status')}")
        if not output.get("image_ref") or not output.get("final_build", "").startswith("success"):
            raise E11Error("DRAFT did not produce a successful image")
        if not all((out / "draft" / round_["log_file"]).is_file() for round_ in rounds):
            raise E11Error("DRAFT log_file is missing")
        summary["checks"]["draft"] = {"rounds": len(rounds), "image_ref": output["image_ref"]}

        status, submitted = submit(remote_base, repair_request, MDFIXER_PORT)
        if status != 202:
            raise E11Error(f"REPAIR submit returned {status}: {submitted}")
        repair_job_id = submitted["job_id"]
        jobs.append(("mdfixer", repair_job_id))
        repair_job = poll(remote_base, repair_job_id, MDFIXER_PORT)
        save_job(out / "repair", "request", repair_request)
        save_job(out / "repair", "job", repair_job)
        copy_logs("mdfixer", repair_job_id, out / "repair")
        repair_output = repair_job.get("output") or {}
        if repair_job.get("status") != "SUCCEEDED" or not repair_output.get("accepted"):
            raise E11Error(f"REPAIR was not accepted: {repair_job.get('status')}")
        validation = repair_output.get("validation") or {}
        if not all(validation.get(key) for key in (
            "patch_applies", "build", "test", "header_change", "no_missing", "redundant_preserved"
        )):
            raise E11Error(f"REPAIR validation failed: {validation}")
        if not (repair_output.get("recheck", {}).get("incremental", {}).get("ok")):
            raise E11Error("REPAIR incremental recheck failed")
        (out / "repair" / "candidate.patch").write_text(repair_output.get("git_patch", ""), encoding="utf-8")
        (out / "repair" / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary["checks"]["repair"] = validation

        # Idempotency: same request returns the same job, changed commit conflicts.
        status, submitted_again = submit(remote_base, repair_request, MDFIXER_PORT)
        if status != 202 or submitted_again.get("job_id") != repair_job_id:
            raise E11Error("idempotent resubmit did not return the same job")
        changed = json.loads(json.dumps(repair_request))
        changed["input"]["commit"] = manifest["baseline_commit"]
        changed["input"]["repository"]["commit"] = manifest["baseline_commit"]
        status, conflict = submit(remote_base, changed, MDFIXER_PORT)
        if status != 409:
            raise E11Error(f"idempotency conflict returned {status}: {conflict}")
        summary["checks"]["idempotency"] = "ok"

        # Path boundary is rejected before a job is created.
        bad = json.loads(json.dumps(repair_request))
        bad["input"]["md_report_uri"] = "artifact://pair07/../report.json"
        bad["input"]["idempotency_key"] = "e11-fault-path"
        status, rejected = submit(remote_base, bad, MDFIXER_PORT)
        if status != 400:
            raise E11Error(f"path boundary returned {status}: {rejected}")
        summary["checks"]["path_boundary"] = "ok"

        for case in prepare_faults(report, repair_request):
            name, case_report, case_request, code = case[:4]
            case_request["input"]["idempotency_key"] = f"e11-fault-{name}-{run_id}"
            suffix = case[4] if len(case) > 4 else ".json"
            target = f"{MDFIXER_DATA}/artifacts/pair07/e11-target/{name}{suffix}"
            write_remote(target, (json.dumps(case_report, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode())
            status, submitted = submit(remote_base, case_request, MDFIXER_PORT)
            if status != 202:
                raise E11Error(f"fault {name} submit returned {status}")
            job_id = submitted["job_id"]
            jobs.append(("mdfixer", job_id))
            job = poll(remote_base, job_id, MDFIXER_PORT)
            save_job(out / "faults" / name, "request", case_request)
            save_job(out / "faults" / name, "job", job)
            error = (job.get("error") or {}).get("code")
            if job.get("status") != "FAILED" or error != code:
                raise E11Error(f"fault {name}: {job.get('status')} {error}")
            summary["faults"][name] = code

        # Macro style uses the same deployed MDFixer and checks the patch location.
        status, submitted = submit(remote_base, macro_request, MDFIXER_PORT)
        if status != 202:
            raise E11Error(f"macro submit returned {status}: {submitted}")
        macro_job_id = submitted["job_id"]
        jobs.append(("mdfixer", macro_job_id))
        macro_job = poll(remote_base, macro_job_id, MDFIXER_PORT)
        save_job(out / "style-macro", "request", macro_request)
        save_job(out / "style-macro", "job", macro_job)
        macro_output = macro_job.get("output") or {}
        if macro_job.get("status") != "SUCCEEDED" or not macro_output.get("accepted"):
            raise E11Error("macro repair was not accepted")
        patch = macro_output.get("git_patch", "")
        if "config.h" not in patch or "DEPS" not in patch:
            raise E11Error("macro patch does not update DEPS")
        (out / "style-macro" / "candidate.patch").write_text(patch, encoding="utf-8")
        summary["checks"]["macro"] = "ok"
        summary["status"] = "SUCCEEDED"
        return summary
    finally:
        cleanup(remote_base, jobs, draft_job_id)


def cleanup(remote_base: str, jobs: list[tuple[str, str]], draft_job_id: str) -> None:
    commands = [f"docker rm -f {shlex.quote(CONTAINER)} >/dev/null 2>&1 || true"]
    for service, job_id in jobs:
        data = DRAFT_DATA if service == "draft" else MDFIXER_DATA
        commands.append(f"rm -rf {shlex.quote(data + '/jobs/' + job_id)}")
    commands.extend([
        f"rm -rf {shlex.quote(MDFIXER_DATA + '/artifacts/pair07/e11-target')}",
        f"rm -rf {shlex.quote(MDFIXER_DATA + '/artifacts/pair07/e11-macro')}",
        f"rm -rf {shlex.quote(remote_base)}",
    ])
    if draft_job_id:
        commands.append(
            "docker image ls --format '{{.Repository}}:{{.Tag}}' | "
            f"grep -E '^b07-draft:{draft_job_id}-' | xargs -r docker image rm -f >/dev/null 2>&1 || true"
        )
    for command in commands:
        try:
            remote(command)
        except E11Error:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the E11 B-track integration.")
    parser.add_argument("--out", type=Path, help="evidence directory")
    args = parser.parse_args(argv)
    out = (args.out or (ROOT / "work" / "e11" / now_id())).resolve()
    if out.exists():
        print(f"run_e11: evidence directory already exists: {out}", file=sys.stderr)
        return 2
    try:
        env = read_env(ROOT / ".env")
        missing = [key for key in LLM_KEYS if not env.get(key)]
        if missing:
            raise E11Error("missing .env keys: " + ", ".join(missing))
        with tempfile.TemporaryDirectory(prefix="e11-run-") as temporary:
            summary = run(Path(temporary), out, env)
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"E11_SUCCEEDED {out}")
        return 0
    except (E11Error, OSError, KeyError, ValueError, subprocess.SubprocessError) as exc:
        out.mkdir(parents=True, exist_ok=True)
        (out / "failure.txt").write_text(str(exc) + "\n", encoding="utf-8")
        print(f"run_e11: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
