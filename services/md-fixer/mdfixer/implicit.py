"""Implicit E9 candidates: compiler .d scheme or a bounded LLM proposal."""

from __future__ import annotations

import re

from .guard import RepairError, relative_path
from .llm import LLMAdapter
from .makefile import Makefile, Rule, references, reference_usage
from .plan import Change
from .report import Finding


def _simple(document: Makefile, rule: Rule) -> bool:
    if rule.targets != ("%.o",) or rule.prerequisites != ("%.c",) or rule.order_only or len(rule.recipes) != 1:
        return False
    if not {"CC", "CFLAGS", "OBJS"} <= document.variables.keys():
        return False
    if document.expand("$(CC)") not in {"cc", "gcc", "clang"}:
        return False
    if any(reference_usage(line)[1] for line in document.text.splitlines() if line.startswith("\t")):
        return False  # Computed recipe names could alias a newly introduced variable.
    recipe = document.text.splitlines()[rule.recipes[0] - 1]
    if not re.fullmatch(r"\t\$\(CC\) (?:\$\(CPPFLAGS\) )?\$\(CFLAGS\) -c \$< -o \$@", recipe):
        return False
    clean = [r for r in document.rules if r.targets == ("clean",)]
    return (
        len(clean) == 1 and len(clean[0].recipes) == 1
        and document.text.splitlines()[clean[0].recipes[0] - 1] == "\trm -f $(TARGET) $(OBJS)"
    )


def _fresh(document: Makefile, name: str) -> str:
    used = set(references(document.text))
    while name in document.variables or name in used:
        name = "MDFIXER_" + name
    return name


def _object_definitions_end(document: Makefile) -> int:
    """A := depfile list must follow every definition used to expand OBJS."""
    pending = ["OBJS"]
    needed: set[str] = set()
    while pending:
        name = pending.pop()
        if name not in needed:
            needed.add(name)
            pending.extend(references(document.variables[name].expression))
    return max(document.variables[name].end_line for name in needed)


def _depfile_candidate(text: str, document: Makefile, findings: tuple[Finding, ...]) -> tuple[str, list[Change]]:
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    flags = _fresh(document, "DEPFLAGS")
    depvar = _fresh(document, "DEPS")
    targets = tuple(sorted({item.target for item in findings}))
    objects = tuple(document.expand("$(OBJS)").split())
    depvalue = "$(OBJS:.o=.d)" if set(objects) == set(targets) else " ".join(target[:-2] + ".d" for target in targets)
    clean = next(r for r in document.rules if r.targets == ("clean",))
    for rule in {item.rule for item in findings}:
        line = rule.recipes[0] - 1
        lines[line] = lines[line].replace("$(CFLAGS) -c", f"$(CFLAGS) $({flags}) -c", 1)
    clean_line = clean.recipes[0] - 1
    lines[clean_line] += f" $({depvar})"
    insertions: dict[int, list[str]] = {}
    insertions.setdefault(document.variables["CFLAGS"].end_line, []).append(f"{flags} = -MMD -MP")
    object_line = document.text.splitlines()[document.variables["OBJS"].line - 1]
    alignment = re.match(r"\s*OBJS(\s+)", object_line)
    spacing = alignment.group(1) if alignment and len(depvar) == 4 else " "
    definition_end = _object_definitions_end(document)
    insertions.setdefault(definition_end, []).append(f"{depvar}{spacing}:= {depvalue}")
    # Keep the original layout where safe. If clean is the first goal or
    # precedes the object definitions, include at EOF so the list is initialized
    # and an included depfile cannot replace the original default goal.
    default = next(r for r in document.rules if not r.implicit and not r.targets[0].startswith("."))
    include_at = clean.line - 1
    if include_at < definition_end or include_at <= default.end_line:
        include_at = len(lines)
    insertions.setdefault(include_at, []).extend([f"-include $({depvar})", ""])
    for index in sorted(insertions, reverse=True):
        lines[index:index] = insertions[index]
    changes = [Change(
        item.finding_id, item.target, item.dependency, "implicit", "depfile", item.rule.line,
        "generate and include compiler .d files; retain existing prerequisites and clean generated files",
        depvar, objects,
    ) for item in findings]
    return newline.join(lines) + newline, changes


def _check_llm_candidate(before: str, after: str, findings: tuple[Finding, ...]) -> str:
    newline = "\r\n" if "\r\n" in before else "\n"
    original = before.replace("\r\n", "\n")
    candidate = after.replace("\r\n", "\n")
    if not candidate.startswith(original) or not candidate.endswith("\n"):
        raise RepairError("UNSUPPORTED_TARGET", "LLM must preserve all existing declarations and recipes")
    requested = {(item.target, item.dependency) for item in findings}
    added: set[tuple[str, str]] = set()
    for line in candidate[len(original):].splitlines():
        if not line.strip():
            continue
        if line.startswith((" ", "\t")) or line.count(":") != 1:
            raise RepairError("UNSUPPORTED_TARGET", "LLM may only append prerequisite declarations")
        target, dependencies = line.split(":")
        target = relative_path(target)
        if not dependencies.split():
            raise RepairError("UNSUPPORTED_TARGET", "LLM returned an empty declaration")
        for dependency in dependencies.split():
            pair = (target, relative_path(dependency))
            if pair not in requested or pair in added:
                raise RepairError("UNSUPPORTED_TARGET", "LLM added an unrequested or duplicate dependency")
            added.add(pair)
    if added != requested:
        raise RepairError("UNSUPPORTED_TARGET", "LLM candidate does not cover every requested missing edge")
    return candidate.replace("\n", newline)


def is_simple_rule(document: Makefile, rule: Rule) -> bool:
    return _simple(document, rule)


def generate_implicit(text: str, document: Makefile, findings: tuple[Finding, ...], *, llm=None) -> tuple[str, list[Change]]:
    simple = tuple(item for item in findings if _simple(document, item.rule))
    complex_findings = tuple(item for item in findings if item not in simple)
    changes: list[Change] = []
    if complex_findings and llm is None:
        raise RepairError("LLM_REQUIRED", "complex implicit rules require an injected LLM client")
    if simple:
        text, changes = _depfile_candidate(text, document, simple)
    if complex_findings:
        adapter = llm if isinstance(llm, LLMAdapter) else LLMAdapter(llm)
        proposed = adapter.propose(text, complex_findings)
        text = _check_llm_candidate(text, proposed, complex_findings)
        changes.extend(Change(
            item.finding_id, item.target, item.dependency, "implicit", "llm_prerequisites", item.rule.line,
            "LLM supplied concrete prerequisites; original implicit recipe and all existing text are retained",
            affected_targets=(item.target,),
        ) for item in complex_findings)
    return text, changes
