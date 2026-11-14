"""Static declaration graph for the E9 Make subset; never invokes make.

Supports =, :=, ?=, +=, simple variable references, suffix substitutions,
continuations, comments, order-only prerequisites and single pattern rules.
Includes, conditionals, eval/functions, target-specific assignments and other
dynamic declarations are rejected rather than executed or silently guessed.
Recipes are retained as opaque text.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

from .guard import RepairError

_ASSIGN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(:=|\?=|\+=|=)\s*(.*)$")
_REF = re.compile(r"\$\(([A-Za-z_][A-Za-z0-9_]*)(?::([^=():{}\s]*)=([^():{}\s]*))?\)|\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def references(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(m.group(1) or m.group(4) for m in _REF.finditer(text)))


def _comment(text: str) -> str:
    # Escaped filenames/comments are outside this bounded subset.
    return text.split("#", 1)[0].rstrip()


@dataclass(frozen=True)
class Variable:
    name: str
    operator: str
    value: str
    line: int
    end_line: int
    simple: bool = False
    expression: str = ""


@dataclass(frozen=True)
class Rule:
    targets: tuple[str, ...]
    prerequisites: tuple[str, ...]
    order_only: tuple[str, ...]
    target_text: str
    prerequisite_text: str
    line: int
    end_line: int
    recipes: tuple[int, ...] = ()

    @property
    def implicit(self) -> bool:
        return any("%" in target for target in self.targets)

    def stem(self, target: str) -> str | None:
        for pattern in self.targets:
            if "%" not in pattern:
                if pattern == target:
                    return ""
                continue
            prefix, suffix = pattern.split("%")
            if target.startswith(prefix) and target.endswith(suffix) and len(target) > len(prefix) + len(suffix):
                return target[len(prefix):len(target) - len(suffix) if suffix else None]
        return None


@dataclass
class Makefile:
    text: str
    variables: dict[str, Variable] = field(default_factory=dict)
    rules: list[Rule] = field(default_factory=list)
    graph: dict[str, set[str]] = field(default_factory=dict)

    def expand(self, text: str, stack: tuple[str, ...] = ()) -> str:
        def replace(match: re.Match) -> str:
            name = match.group(1) or match.group(4)
            if name in stack:
                raise RepairError("UNSUPPORTED_TARGET", "recursive variable cycle")
            variable = self.variables.get(name)
            if variable is None:
                raise RepairError("UNSUPPORTED_TARGET", f"undefined declaration variable: {name}")
            value = variable.value if variable.simple else self.expand(variable.value, (*stack, name))
            if match.group(2) is not None:
                old, new = match.group(2), match.group(3)
                if "%" in old or "%" in new or not old:
                    raise RepairError("UNSUPPORTED_TARGET", "unsupported substitution reference")
                value = " ".join(word[:-len(old)] + new if word.endswith(old) else word for word in value.split())
            return value

        result = _REF.sub(replace, text)
        if "$" in result or any(c in result for c in "\\;`{}()"):
            raise RepairError("UNSUPPORTED_TARGET", "dynamic Make expressions are unsupported")
        return result

    def distance(self, start: str, end: str) -> int | None:
        queue = deque([(start, 0)])
        seen = {start}
        while queue:
            node, distance = queue.popleft()
            if node == end:
                return distance
            for child in sorted(self.graph.get(node, ())):
                if child not in seen:
                    seen.add(child)
                    queue.append((child, distance + 1))
        return None

    def affected_targets(self, variable: str) -> tuple[str, ...]:
        node = f"variable:{variable}"
        return tuple(sorted({
            target for rule in self.rules for target in rule.targets
            if self.distance(f"target:{target}", node) is not None
        }))

    def rule_at(self, line: int, target: str) -> Rule:
        matches = [r for r in self.rules if r.line <= line <= r.end_line and r.stem(target) is not None]
        if len(matches) != 1:
            raise RepairError("UNSUPPORTED_TARGET", "report position does not identify the requested target")
        rule = matches[0]
        if len(rule.targets) != 1 or rule.targets[0].startswith("."):
            raise RepairError("UNSUPPORTED_TARGET", "multi-target and special rules cannot be repaired")
        if any(r is not rule and r.stem(target) is not None for r in self.rules):
            raise RepairError("UNSUPPORTED_TARGET", "ambiguous or split target declarations")
        return rule


def parse_makefile(text: str) -> Makefile:
    document = Makefile(text)
    lines = text.splitlines()
    raw_rules: list[tuple[str, str, int, int, list[int]]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        start = index + 1
        index += 1
        if line.startswith("\t"):
            if not raw_rules:
                raise RepairError("UNSUPPORTED_TARGET", "recipe without a rule")
            raw_rules[-1][4].append(start)
            continue
        while line.rstrip().endswith("\\"):
            if index >= len(lines):
                raise RepairError("UNSUPPORTED_TARGET", "unterminated continuation")
            line = line.rstrip()[:-1] + " " + lines[index].lstrip()
            index += 1
        declaration = _comment(line).strip()
        if not declaration:
            continue
        assignment = _ASSIGN.fullmatch(declaration)
        if assignment:
            name, operator, value = assignment.groups()
            if name in {"MAKEFLAGS", "SHELL", ".RECIPEPREFIX", ".DEFAULT_GOAL"}:
                raise RepairError("UNSUPPORTED_TARGET", "Make control variables are unsupported")
            old = document.variables.get(name)
            if operator == "?=" and old is not None:
                continue
            simple = operator == ":=" or (operator == "+=" and old is not None and old.simple)
            expression = value
            if simple:
                value = document.expand(value)
            if operator == "+=" and old is not None:
                value = (old.value + " " + value).strip()
                expression = (old.expression + " " + expression).strip()
            document.variables[name] = Variable(name, operator, value, start, index, simple, expression)
            continue
        if declaration.startswith(("include ", "-include ", "sinclude ", "ifeq", "ifneq", "ifdef", "ifndef", "else", "endif", "define ", "endef", "export ", "override ", "vpath ")):
            raise RepairError("UNSUPPORTED_TARGET", "includes, conditionals and directives are unsupported")
        # Colons inside a substitution reference are not rule separators.
        masked = _REF.sub("VAR", declaration)
        if masked.count(":") != 1 or ";" in masked or "&:" in masked:
            raise RepairError("UNSUPPORTED_TARGET", "unsupported Make declaration")
        colon = next(i for i, c in enumerate(declaration) if c == ":" and not any(m.start() <= i < m.end() for m in _REF.finditer(declaration)))
        lhs, rhs = declaration[:colon].strip(), declaration[colon + 1:].strip()
        if not lhs or "=" in _REF.sub("VAR", rhs):
            raise RepairError("UNSUPPORTED_TARGET", "unsupported rule or target-specific variable")
        raw_rules.append((lhs, rhs, start, index, []))

    for variable in document.variables.values():
        document.expand(f"$({variable.name})")  # Reject cycles/functions even in unused declarations.
        document.graph[f"variable:{variable.name}"] = {
            f"variable:{ref}" for ref in references(variable.expression)
        } | {f"file:{word}" for word in document.expand(f"$({variable.name})").split()}
    for lhs, rhs, start, end, recipes in raw_rules:
        refs = (*references(lhs), *references(rhs))
        reachable = list(refs)
        seen: set[str] = set()
        while reachable:
            ref = reachable.pop()
            if ref in seen:
                continue
            seen.add(ref)
            variable = document.variables.get(ref)
            if variable is None or variable.line > start:
                raise RepairError("UNSUPPORTED_TARGET", "late or missing declaration variable")
            reachable.extend(references(variable.expression))
        if rhs.count("|") > 1:
            raise RepairError("UNSUPPORTED_TARGET", "invalid order-only prerequisites")
        normal, _, order = rhs.partition("|")
        targets = tuple(document.expand(lhs).split())
        prerequisites = tuple(document.expand(normal).split())
        order_only = tuple(document.expand(order).split())
        if any(t.count("%") > 1 for t in targets):
            raise RepairError("UNSUPPORTED_TARGET", "unsupported pattern")
        rule = Rule(targets, prerequisites, order_only, lhs, normal.strip(), start, end, tuple(recipes))
        document.rules.append(rule)
        for target in targets:
            document.graph.setdefault(f"target:{target}", set()).update(
                {f"variable:{ref}" for ref in references(normal)}
                | {f"file:{word}" for word in prerequisites}
            )
    return document
