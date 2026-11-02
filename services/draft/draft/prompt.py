"""Pure prompt construction with evidence line numbers."""

from __future__ import annotations

from dataclasses import dataclass

PROMPT_VERSION = "v1"
TAIL_LINE_LIMIT = 50
MAX_MATCHED_LINES = 100
MAX_README_CHARS = 20000
DEFAULT_KEYWORDS = (
    "error", "unknown", "missing", "not specified", "no such file or directory",
    "file not found", "failed to execute", "execution failed",
    "unable to locate package", "no matching distribution found for",
)


@dataclass(frozen=True)
class ErrorExcerpt:
    matched: list[tuple[int, str]]
    tail: list[str]

    def render(self) -> str:
        matched = "\n".join(f"{number}: {line}" for number, line in self.matched)
        return f"Keyword matches (original line numbers):\n{matched or '(none)'}\n\nLog tail:\n" + "\n".join(self.tail)


def locate_error(log: str, *, keywords=DEFAULT_KEYWORDS, tail_limit=TAIL_LINE_LIMIT) -> ErrorExcerpt:
    if tail_limit < 0:
        raise ValueError("tail_limit must be nonnegative")
    lines = log.splitlines()
    terms = tuple(term.lower() for term in keywords)
    matched = [(i, line) for i, line in enumerate(lines, 1) if any(term in line.lower() for term in terms)]
    return ErrorExcerpt(matched[:MAX_MATCHED_LINES], lines[-tail_limit:] if tail_limit else [])


def build_error_prompt(dockerfile_text: str, failure_log: str) -> str:
    return (
        f"DRAFT repair prompt {PROMPT_VERSION}\n"
        "修复下面 Dockerfile 的构建或运行失败。输出 3 个候选 Dockerfile，"
        "每个候选使用独立的 ```Dockerfile 围栏。\n"
        "保留基础镜像（含 digest）、WORKDIR、COPY、构建命令及其顺序；"
        "不得改动运行命令 CMD/ENTRYPOINT，也不得添加多阶段构建。"
        "可在原构建命令之前新增 RUN 安装缺失依赖。\n\n"
        f"Current Dockerfile:\n```Dockerfile\n{dockerfile_text}\n```\n\n"
        f"Failure evidence:\n{locate_error(failure_log).render()}\n"
    )


def build_context_prompt(dockerfile_text: str, failure_log: str, readme_text: str) -> str:
    readme = readme_text[:MAX_README_CHARS]
    return build_error_prompt(dockerfile_text, failure_log) + f"\nProject README:\n{readme}\n"


def build_choice_prompt(candidates, failure_log: str) -> str:
    """Ask the model to choose one already-extracted candidate by index."""
    blocks = "\n\n".join(
        f"Candidate {candidate.index}:\n```Dockerfile\n{candidate.text}\n```"
        for candidate in candidates
    )
    return (
        f"DRAFT candidate choice {PROMPT_VERSION}\n"
        "下面是通过基础校验的候选 Dockerfile。请根据失败证据选择最可能修复问题的一个。"
        "只输出候选序号，例如 `2`，不要输出其它内容。\n\n"
        f"{blocks}\n\n"
        f"Failure evidence:\n{locate_error(failure_log).render()}\n"
    )
