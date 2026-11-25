"""Turn DRAFT progress events into safe console lines.

Reads JSON lines from stdin. Unknown or malformed lines are ignored; the
caller keeps the full raw stream in run.log.
"""

from __future__ import annotations

import json
import sys


def _value(event: dict, key: str, default: str = "-") -> str:
    value = event.get(key)
    return str(value) if value is not None else default


def _mb(value) -> str:
    try:
        return f"{float(value) / (1024 ** 2):.1f}"
    except (TypeError, ValueError):
        return "-"


def format_event(event: dict) -> str | None:
    kind = event.get("event")
    status = event.get("status")
    iteration = event.get("iteration")

    if kind == "initial_build":
        if status == "start":
            return "      初始构建: 开始"
        if status == "running":
            return f"      初始构建: 进行中, 已运行 {_value(event, 'elapsed_s')}s"
        if status == "end":
            return (
                f"      初始构建: exit={_value(event, 'exit_code')}, "
                f"failure={_value(event, 'failure_class')}"
            )
    elif kind == "initial_verify":
        if status == "start":
            return "      初始验证: 开始"
        if status == "end":
            return (
                f"      初始验证: exit={_value(event, 'exit_code')}, "
                f"output_matches={_value(event, 'output_matches')}"
            )
    elif kind == "iteration":
        if status == "start":
            return f"      第 {iteration} 轮: 开始"
        if status == "end":
            return (
                f"      第 {iteration} 轮: 结束, "
                f"build_exit={_value(event, 'build_exit_code')}, "
                f"verified={_value(event, 'verification_ok')}"
            )
    elif kind == "prompt":
        if status == "start":
            return f"      第 {iteration} 轮: 生成候选"
        if status == "end":
            return f"      第 {iteration} 轮: 候选已返回"
    elif kind == "selected":
        return (
            f"      第 {iteration} 轮: 选中候选 "
            f"{_value(event, 'index')} ({_value(event, 'rule')})"
        )
    elif kind == "build":
        if status == "start":
            return f"      第 {iteration} 轮: 构建开始"
        if status == "running":
            return f"      第 {iteration} 轮: 构建中, 已运行 {_value(event, 'elapsed_s')}s"
        if status == "end":
            return (
                f"      第 {iteration} 轮: 构建结束, exit={_value(event, 'exit_code')}, "
                f"failure={_value(event, 'failure_class')}"
            )
    elif kind == "apt_get":
        return (
            f"      第 {iteration} 轮: apt 已获取 {_value(event, 'packages')} 个包, "
            f"{_mb(event.get('bytes'))} MB"
        )
    elif kind == "apt_total":
        return f"      第 {iteration} 轮: apt 需要下载 {_mb(event.get('bytes'))} MB"
    elif kind == "apt_fetched":
        return f"      第 {iteration} 轮: apt 已下载 {_mb(event.get('bytes'))} MB"
    elif kind == "apt_package":
        return f"      第 {iteration} 轮: apt 处理 {_value(event, 'package')}"
    elif kind == "build_step":
        return f"      第 {iteration} 轮: 构建步骤 [{_value(event, 'step')}]"
    elif kind == "verify":
        if status == "start":
            return f"      第 {iteration} 轮: 验证开始"
        if status == "end":
            return (
                f"      第 {iteration} 轮: 验证结束, exit={_value(event, 'exit_code')}, "
                f"output_matches={_value(event, 'output_matches')}"
            )
    elif kind == "done":
        return (
            f"      完成: status={_value(event, 'status')}, "
            f"error={_value(event, 'error_code')}, rounds={_value(event, 'rounds')}"
        )
    return None


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        rendered = format_event(event)
        if rendered:
            print(rendered, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
