"""Extract, validate and select Dockerfiles without I/O."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Container, Sequence

EXPECTED_CANDIDATES = 3
_INSTRUCTION = re.compile(r"^([A-Za-z]+)\s+(.+)$")
_KNOWN = {
    "FROM", "ARG", "RUN", "COPY", "WORKDIR", "CMD", "ENV", "ENTRYPOINT", "ADD",
    "LABEL", "USER", "EXPOSE", "VOLUME", "STOPSIGNAL", "HEALTHCHECK", "SHELL", "ONBUILD",
}


class CandidateError(Exception):
    """The reply contains no usable candidate."""


@dataclass(frozen=True)
class Candidate:
    index: int
    text: str
    sha256: str
    normalized: str
    fenced: bool


@dataclass(frozen=True)
class Selection:
    candidate: Candidate
    rule: str
    detail: str
    rejected: tuple[tuple[int, str], ...]


def normalize_continuations(text: str) -> str:
    lines = []
    pending = ""
    for line in text.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        continued = line.endswith("\\")
        pending += line[:-1].rstrip() if continued else line
        if continued:
            pending += " "
        else:
            lines.append(pending)
            pending = ""
    if pending.strip():
        lines.append(pending.rstrip())
    return "\n".join(lines)


def _trim(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def extract_candidates(reply: str, *, expected: int = EXPECTED_CANDIDATES) -> list[Candidate]:
    if expected <= 0:
        raise ValueError("expected candidate count must be positive")
    # Keep offsets so fenced and unfenced candidates retain their reply order.
    blocks = []
    outside = list(reply)
    for match in re.finditer(r"^\s*```[^\n`]*\n(.*?)^\s*```[^\S\n]*$", reply, re.MULTILINE | re.DOTALL):
        text = _trim(match.group(1))
        if re.search(r"^\s*(?:FROM|ARG)\s+", text, re.MULTILINE | re.IGNORECASE):
            blocks.append((match.start(), text, True))
        outside[match.start():match.end()] = " " * (match.end() - match.start())
    if len(blocks) < expected:
        plain = "".join(outside)
        current = []
        start = 0
        continued = False

        def flush():
            if current and any(re.match(r"\s*FROM\s+", line, re.IGNORECASE) for line in current):
                blocks.append((start, _trim("\n".join(current)), False))
            current.clear()

        offset = 0
        for line in plain.splitlines(keepends=True):
            stripped = line.strip()
            instruction = _INSTRUCTION.match(stripped)
            name = instruction.group(1).upper() if instruction else None
            if name == "FROM" and not continued and any(re.match(r"\s*FROM\s+", item, re.IGNORECASE) for item in current):
                flush()
            if continued or name in _KNOWN or (current and (not stripped or stripped.startswith("#"))):
                if not current:
                    start = offset
                current.append(line.rstrip("\r\n"))
                if stripped and not stripped.startswith("#"):
                    continued = stripped.endswith("\\")
            else:
                flush()
                continued = False
            offset += len(line)
        flush()
    if not blocks:
        raise CandidateError("reply contains no Dockerfile candidates")
    return [
        Candidate(i, text, hashlib.sha256(text.encode("utf-8")).hexdigest(), normalize_continuations(text), fenced)
        for i, (_, text, fenced) in enumerate(sorted(blocks)[:expected], 1)
    ]


def _instructions(text: str) -> list[tuple[str, str]]:
    result = []
    for line in normalize_continuations(text).splitlines():
        match = _INSTRUCTION.match(line)
        result.append((match.group(1).upper(), match.group(2)) if match else ("INVALID", line))
    return result


def check_candidate(text: str, baseline: str) -> list[str]:
    instructions, original = _instructions(text), _instructions(baseline)
    reasons = []
    if any(name not in _KNOWN for name, _ in instructions):
        reasons.append("unknown or malformed Dockerfile instruction")
    first = next((name for name, _ in instructions if name != "ARG"), None)
    if first != "FROM":
        reasons.append("first instruction other than ARG must be FROM")
    sources = [args for name, args in instructions if name == "FROM"]
    if len(sources) != 1 or sources != [args for name, args in original if name == "FROM"]:
        reasons.append("FROM must match baseline exactly; multi-stage builds are unsupported")
    protected = {"WORKDIR", "COPY", "CMD", "ENTRYPOINT"}
    build_commands = {args for name, args in original if name == "RUN" and re.match(r"make(?:\s|$)", args)}

    def fixed(items):
        return [(name, args) for name, args in items if name in protected or (name == "RUN" and args in build_commands)]

    if fixed(instructions) != fixed(original):
        reasons.append("WORKDIR, COPY, RUN make, CMD and ENTRYPOINT must retain their values and order")
    for name in ("CMD", "ENTRYPOINT"):
        if [arg for key, arg in instructions if key == name] != [arg for key, arg in original if key == name]:
            reasons.append(f"{name} must match baseline exactly")
    build_seen = False
    for name, args in instructions:
        if name == "RUN" and args in build_commands:
            build_seen = True
        elif name == "RUN" and build_seen:
            reasons.append("additional RUN instructions must precede RUN make")
            break
    return reasons


def select_candidate(
    candidates: Sequence[Candidate], *, used_sha256: Container[str], error_terms: Sequence[str],
    chooser: Callable[[Sequence[Candidate]], int] | None = None,
) -> Selection:
    remaining = [item for item in candidates if item.sha256 not in used_sha256]
    rejected = [(item.index, "R1: candidate already used") for item in candidates if item.sha256 in used_sha256]
    if not remaining:
        raise CandidateError("R1: no unused valid candidates remain")

    def choose(item, rule, detail):
        others = [(other.index, f"{rule}: not selected") for other in remaining if other.index != item.index]
        return Selection(item, rule, detail, tuple(rejected + others))

    if len(remaining) == 1:
        return choose(remaining[0], "R1", f"R1: only candidate {remaining[0].index} remains")
    groups = defaultdict(list)
    for item in remaining:
        groups[item.normalized].append(item)
    largest = max(len(group) for group in groups.values())
    winners = [group for group in groups.values() if len(group) == largest]
    if largest >= 2 and len(winners) == 1:
        group = winners[0]
        return choose(min(group, key=lambda item: item.index), "R2", f"R2: candidates {[item.index for item in group]} match after continuation normalization")
    terms = [term.lower() for term in error_terms if term.strip()]
    matches = [item for item in remaining if any(term in item.text.lower() for term in terms)]
    if len(matches) == 1:
        return choose(matches[0], "R3", f"R3: only candidate {matches[0].index} mentions an error term")
    index = chooser(tuple(remaining)) if chooser else min(item.index for item in remaining)
    selected = next((item for item in remaining if item.index == index), None)
    if selected is None:
        raise CandidateError(f"R4: chooser returned unavailable candidate index {index}")
    return choose(selected, "R4", f"R4: {'chooser' if chooser else 'lowest index'} selected candidate {index}")
