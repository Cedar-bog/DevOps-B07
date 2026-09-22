#!/usr/bin/env python3
"""E2-B07-010 失败输入校验脚本。

设计要点
- task.schema.json 是唯一真源：本脚本不重述任何约束，只加载并执行它。
- 正向一致性：递归发现 examples/ 下的 Job / 请求体 / artifact 实例并逐条校验，
  任一不通过即视为「样例与 Schema 漂移」。每个样例文件必须至少贡献 1 条 payload，
  否则 FAIL —— 防止静默漏判使套件失去意义。examples/invalid/ 是负例区（如 A07 提供的
  失败样例），不计入正向，由 SKIP 行显式列出，避免被误当成漏判或误当成漂移。
- 失败输入：fixtures/negative/ 下的用例是「变异」而非副本 —— 只记录来源指针与变异操作，
  运行时从当前样例实时派生，故样例更新时用例不会静默失效。每个用例断言两件事：
  被拒绝，且违反点是指定的 JSON 指针 + 关键字；只断言「被拒绝」会掩盖「因别的原因被拒」。
- MD 与工具失败的语义差异（验收 04）：发现（MD/RD）是检测的正常产物，写入 output 的
  ERROR_REPORT，任务保持 SUCCEEDED 且 job.error 为 null；工具/系统失败才写 job.error。
  该语义由 check_semantic_invariants() 以不变式钉住，避免说明与实现漂移。
- $comment 是样例的自描述注解，不是载荷数据，校验前递归剥离。
- 已知例外见 OUT_OF_SCOPE，注明依据，不做隐式放宽。

用法
    uv run docs/contracts/validate.py          # 退出码 0/1，可作 CI 门禁
    uv run pytest docs/contracts/validate.py   # 同一套逻辑，pytest 可收集
"""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

CONTRACTS = Path(__file__).resolve().parent
EXAMPLES = CONTRACTS / "examples"
NEGATIVE_DIR = CONTRACTS / "fixtures" / "negative"
NEGATIVE_EXAMPLE_DIR = "invalid"
SCHEMA_PATH = CONTRACTS / "task.schema.json"

JOB = "job"
REQUEST = "request"
ARTIFACT = "artifact"

JOB_SIG = {"job_id", "job_type", "status"}
ARTIFACT_SIG = {"artifact_id", "type", "uri", "media_type"}

EXEC_TIMEOUT_CODE = "EXEC_4002"

# A 侧产物格式，不在 B 侧 schema 范围内（B 只声明消费侧约束，见 E2-B07-004）。
OUT_OF_SCOPE = {
    ("error_report.json", "/examples/error_report"): "ERROR_REPORT 完整格式由 A 组定义",
    ("error_report.json", "/examples/finding"): "发现记录格式由 A 组定义",
}


@dataclass(frozen=True)
class Violation:
    pointer: str
    keyword: str
    message: str


@dataclass(frozen=True)
class Payload:
    kind: str
    file: str
    pointer: str
    data: Any
    job_type: str | None = None

    @property
    def ref(self) -> str:
        return f"{self.file}#{self.pointer}"


@dataclass(frozen=True)
class FixtureResult:
    fixture: dict
    rejected: bool
    violations: tuple[Violation, ...]

    @property
    def matched(self) -> bool:
        want = (self.fixture["expect"]["pointer"], self.fixture["expect"]["keyword"])
        return any((v.pointer, v.keyword) == want for v in self.violations)

    @property
    def observed(self) -> list[tuple[str, str]]:
        return [(v.pointer, v.keyword) for v in self.violations]


# --------------------------------------------------------------------------
# Schema 与指针工具
# --------------------------------------------------------------------------


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def strip_comments(node: Any) -> Any:
    """剥离自描述样例的 $comment 注解，只保留载荷数据。"""
    if isinstance(node, dict):
        return {k: strip_comments(v) for k, v in node.items() if k != "$comment"}
    if isinstance(node, list):
        return [strip_comments(v) for v in node]
    return node


def _tokens(pointer: str) -> list[str]:
    if pointer in ("", "/"):
        return []
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer.split("/")[1:]]


def _pointer_str(parts: Any) -> str:
    joined = "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts)
    return f"/{joined}" if joined else ""


def resolve_pointer(doc: Any, pointer: str) -> Any:
    current = doc
    for token in _tokens(pointer):
        current = current[int(token)] if isinstance(current, list) else current[token]
    return current


def _parent_of(doc: Any, pointer: str) -> tuple[Any, str]:
    tokens = _tokens(pointer)
    if not tokens:
        raise ValueError("不能对根节点执行变异")
    current = doc
    for token in tokens[:-1]:
        current = current[int(token)] if isinstance(current, list) else current[token]
    return current, tokens[-1]


def apply_mutation(doc: Any, mutate: dict) -> Any:
    """在来源样例的副本上执行单点变异，使每个失败用例只违反一条约束。"""
    mutated = copy.deepcopy(doc)
    parent, token = _parent_of(mutated, mutate["pointer"])
    op = mutate["op"]
    if op == "delete":
        if isinstance(parent, list):
            del parent[int(token)]
        else:
            del parent[token]
    elif op == "set":
        if isinstance(parent, list):
            parent[int(token)] = mutate["value"]
        else:
            parent[token] = mutate["value"]
    else:
        raise ValueError(f"未知的变异操作: {op}")
    return mutated


# --------------------------------------------------------------------------
# 发现层
# --------------------------------------------------------------------------


def classify(node: Any) -> str | None:
    if not isinstance(node, dict):
        return None
    keys = set(node)
    if JOB_SIG <= keys:
        return JOB
    if ARTIFACT_SIG <= keys:
        return ARTIFACT
    if {"job_type", "input"} <= keys and "job_id" not in keys:
        return REQUEST
    if isinstance(node.get("job_type"), str) and isinstance(node.get("request"), dict):
        return REQUEST
    return None


def _walk(node: Any, path: list, file_name: str, out: list[Payload]) -> None:
    if isinstance(node, dict):
        kind = classify(node)
        if kind == JOB:
            out.append(Payload(JOB, file_name, _pointer_str(path), strip_comments(node)))
            return  # Job 的 schema 已覆盖其 output，无需重复识别嵌套 artifact
        if kind == ARTIFACT:
            out.append(Payload(ARTIFACT, file_name, _pointer_str(path), strip_comments(node)))
            return
        if kind == REQUEST:
            for key in ("input", "request"):
                child = node.get(key)
                if isinstance(child, dict):
                    out.append(
                        Payload(
                            REQUEST,
                            file_name,
                            _pointer_str(path + [key]),
                            strip_comments(child),
                            node.get("job_type"),
                        )
                    )
                    return
        for key, value in node.items():
            _walk(value, path + [key], file_name, out)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _walk(value, path + [index], file_name, out)


def _sample_relpaths(negative: bool) -> list[str]:
    """examples/ 下的样例按相对路径收集；invalid/ 是负例区，与正向样例分流。"""
    paths = (
        path
        for path in EXAMPLES.rglob("*.json")
        if (NEGATIVE_EXAMPLE_DIR in path.relative_to(EXAMPLES).parts) == negative
    )
    return sorted(str(path.relative_to(EXAMPLES)).replace("\\", "/") for path in paths)


def positive_sample_files() -> list[str]:
    return _sample_relpaths(negative=False)


def negative_sample_files() -> list[str]:
    return _sample_relpaths(negative=True)


def discover_payloads() -> list[Payload]:
    payloads: list[Payload] = []
    for rel in positive_sample_files():
        document = json.loads((EXAMPLES / rel).read_text(encoding="utf-8"))
        _walk(document, [], rel, payloads)
    return payloads


def unrecognized_sample_files(payloads: list[Payload]) -> list[str]:
    recognized = {p.file for p in payloads}
    return [rel for rel in positive_sample_files() if rel not in recognized]


# --------------------------------------------------------------------------
# 校验层
# --------------------------------------------------------------------------


def input_branch(schema: dict, job_type: str | None) -> dict | None:
    for entry in schema.get("allOf", []):
        condition = entry.get("if", {}).get("properties", {}).get("job_type", {})
        if condition.get("const") == job_type:
            return entry["then"]["properties"]["input"]
    return None


def check_semantic_invariants(job: dict) -> list[Violation]:
    """MD/RD 发现 ≠ 工具失败：只有系统执行失败才写 job.error（见 E2-B07-002）。"""
    status = job.get("status")
    error = job.get("error")
    violations: list[Violation] = []

    if status == "SUCCEEDED" and error is not None:
        violations.append(
            Violation(
                "/error",
                "null-required",
                "检测正常完成即为成功：发现（MD/RD）写入 output 的 ERROR_REPORT，"
                "job.error 必须为 null（发现 ≠ 工具失败）",
            )
        )
    if status == "FAILED" and error is None:
        violations.append(
            Violation(
                "/error",
                "required",
                "FAILED 属系统执行失败，原因必须写入 job.error"
                "（ENV_3002 / EXEC_4002 / ANALYSIS_5001）",
            )
        )
    if status == "TIMED_OUT":
        if error is None:
            violations.append(Violation("/error", "required", "TIMED_OUT 必须带 job.error"))
        elif error.get("code") != EXEC_TIMEOUT_CODE:
            violations.append(
                Violation(
                    "/error/code",
                    "const",
                    f"TIMED_OUT 的 job.error.code 必须为 {EXEC_TIMEOUT_CODE}",
                )
            )
    return violations


def validate_payload(payload: Payload, schema: dict) -> list[Violation]:
    if payload.kind == JOB:
        target = schema
    elif payload.kind == ARTIFACT:
        target = {"$ref": "#/$defs/artifact"}
    else:
        target = input_branch(schema, payload.job_type)
        if target is None:
            return [Violation("", "job_type", f"没有 job_type={payload.job_type!r} 的输入分支")]

    validator = Draft202012Validator(schema).evolve(schema=target)
    return [
        Violation(_pointer_str(error.absolute_path), error.validator, error.message)
        for error in validator.iter_errors(payload.data)
    ]


def all_violations(payload: Payload, schema: dict) -> list[Violation]:
    violations = validate_payload(payload, schema)
    if payload.kind == JOB:
        violations += check_semantic_invariants(payload.data)
    return violations


# --------------------------------------------------------------------------
# 失败输入用例
# --------------------------------------------------------------------------


def load_fixtures() -> list[dict]:
    return sorted(
        (json.loads(path.read_text(encoding="utf-8")) for path in NEGATIVE_DIR.glob("*.json")),
        key=lambda fixture: fixture["id"],
    )


def load_fixture(fixture_id: str) -> dict:
    for fixture in load_fixtures():
        if fixture["id"] == fixture_id:
            return fixture
    raise KeyError(f"没有 id 为 {fixture_id!r} 的失败用例")


def run_fixture(fixture: dict, schema: dict) -> FixtureResult:
    source = EXAMPLES / fixture["source"]["file"]
    document = json.loads(source.read_text(encoding="utf-8"))
    base = strip_comments(resolve_pointer(document, fixture["source"]["pointer"]))
    mutated = apply_mutation(base, fixture["mutate"])
    if classify(mutated) != JOB:
        raise ValueError(f"{fixture['id']}: source 指针未指向 Job")
    payload = Payload(
        JOB,
        fixture["source"]["file"],
        fixture["source"]["pointer"],
        mutated,
        mutated.get("job_type"),
    )
    violations = all_violations(payload, schema)
    return FixtureResult(fixture, rejected=bool(violations), violations=tuple(violations))


def semantic_explanation() -> str:
    return "\n".join(
        [
            "MD/RD 发现是检测的正常产物，不是失败：",
            "  检测器跑完、结论可用 → 任务 SUCCEEDED，发现写入 output 的 ERROR_REPORT.findings",
            "  因此 SUCCEEDED 时 job.error 必须为 null（发现 ≠ 失败）",
            "工具/系统失败才写入 job.error：",
            "  ENV_3002 镜像构建失败 / EXEC_4002 任务超时 / ANALYSIS_5001 分析器失败",
            "  FAILED、TIMED_OUT 必须带 job.error；TIMED_OUT 的 code 固定为 EXEC_4002",
            "依据：E2-B07-002 状态与错误码约定（examples/status_error.json）",
            "不变式：check_semantic_invariants()  用例：fixtures/negative/succeeded_with_error.json",
        ]
    )


# --------------------------------------------------------------------------
# 报告与入口
# --------------------------------------------------------------------------


def _force_utf8_stdout() -> None:
    """Windows 控制台默认用 GBK(cp936) 编码 stdout，会让中文报告在不同终端/CI 下乱码。

    样例与 Schema 均为 UTF-8，报告也统一按 UTF-8 输出，避免依赖运行机器的区域设置。
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def main() -> int:
    _force_utf8_stdout()
    schema = load_schema()
    failures = 0

    print("== 正向一致性 (conformance) ==")
    positives = discover_payloads()
    for payload in positives:
        violations = all_violations(payload, schema)
        print(f"  {'PASS' if not violations else 'FAIL'}  {payload.ref}  [{payload.kind}]")
        for violation in violations:
            print(
                f"        {violation.pointer or '/'} <- {violation.keyword}: "
                f"{violation.message[:100]}"
            )
        failures += bool(violations)

    skipped = unrecognized_sample_files(positives)
    if skipped:
        print(f"  FAIL  未被识别的样例文件（防静默漏判）: {skipped}")
        failures += len(skipped)

    for rel in negative_sample_files():
        print(
            f"  SKIP  {rel}  [负例区，由 fixtures/negative/ 下的用例覆盖同一拒绝点]"
        )

    for (file_name, pointer), reason in OUT_OF_SCOPE.items():
        print(f"  SKIP  {file_name}#{pointer}  [{reason}]")

    print("\n== 失败输入 (failure inputs) ==")
    fixtures = load_fixtures()
    for fixture in fixtures:
        result = run_fixture(fixture, schema)
        expect = fixture["expect"]
        ok = result.rejected and result.matched
        print(
            f"  {'PASS' if ok else 'FAIL'}  {fixture['id']}"
            f"  →  {expect['pointer']}  {expect['keyword']}  (owner={fixture['owner']})"
        )
        if not ok:
            print(f"        实际: {result.observed or '未被拒绝'}")
            failures += 1

    print("\n== MD 与工具失败的语义差异（验收 04） ==")
    for line in semantic_explanation().splitlines():
        print(f"  {line}")

    print(
        f"\n正向 {len(positives)} 条、失败输入 {len(fixtures)} 条，失败 {failures} 条"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())


# --------------------------------------------------------------------------
# pytest 可收集的一致性套件（与上面的逻辑同源）
# --------------------------------------------------------------------------


def test_positive_samples_conform():
    schema = load_schema()
    failures = []
    for payload in discover_payloads():
        violations = all_violations(payload, schema)
        if violations:
            failures.append((payload.ref, [(v.pointer, v.keyword) for v in violations]))
    assert not failures, f"样例与 Schema 漂移: {failures}"


def test_every_sample_file_contributes_payloads():
    payloads = discover_payloads()
    missing = unrecognized_sample_files(payloads)
    assert not missing, f"未被识别的样例文件（防静默漏判）: {missing}"


def test_unknown_job_type_is_rejected():
    result = run_fixture(load_fixture("job_type_not_in_enum"), load_schema())
    assert result.rejected and result.matched, (
        f"验收 02 失败：期望 /job_type enum，实际 {result.observed}"
    )


def test_removed_baseline_is_rejected():
    result = run_fixture(load_fixture("incremental_missing_baseline"), load_schema())
    assert result.rejected and result.matched, (
        f"验收 03 失败：期望 /input required，实际 {result.observed}"
    )


def test_all_negative_fixtures_rejected_at_expected_violation():
    schema = load_schema()
    failures = []
    for fixture in load_fixtures():
        result = run_fixture(fixture, schema)
        if not (result.rejected and result.matched):
            failures.append((fixture["id"], result.rejected, result.observed))
    assert not failures, f"未按预期违反点被拒绝: {failures}"


def test_semantic_invariants_hold_on_all_samples():
    bad = [
        (payload.ref, check_semantic_invariants(payload.data))
        for payload in discover_payloads()
        if payload.kind == JOB and check_semantic_invariants(payload.data)
    ]
    assert not bad, f"样例违反 MD/工具失败语义（验收 04）: {bad}"


def test_semantic_invariant_actually_fires():
    job = {
        "job_id": "job-x",
        "trace_id": "trace-x",
        "job_type": "FULL_CHECK",
        "status": "SUCCEEDED",
        "execution": {"created_at": "2026-09-21T00:00:00Z"},
        "input": {},
        "output": None,
        "error": {"code": "ANALYSIS_5001", "message": "conflated finding with failure"},
    }
    assert ("/error", "null-required") in [
        (v.pointer, v.keyword) for v in check_semantic_invariants(job)
    ], "不变式未生效：SUCCEEDED 带 error 时未被拒绝"


def test_expectation_mismatch_is_detected():
    fixture = copy.deepcopy(load_fixture("incremental_missing_baseline"))
    fixture["expect"] = {"pointer": "/wrong/place", "keyword": "enum"}
    result = run_fixture(fixture, load_schema())
    assert result.rejected and not result.matched, (
        "期望违反点写错时不应判为匹配，否则断言形同虚设"
    )