"""Read-only E9 entry point: generate_patch(project_root, report, ...).

Returns an in-memory Git patch, candidate text and explainable RepairPlan.
It neither saves artifacts nor applies/builds/accepts a candidate. Callers in
E9-009 and E10 own those operations. Paths in the report are project-relative;
target/dependency paths are relative to the selected Makefile's directory.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Mapping

from .guard import RepairError, read_source
from .makefile import Makefile, parse_makefile, references, reference_usage
from .plan import Change, PatchResult, RepairPlan, Rejection, rejection_from_error
from .report import Finding, Report, load_report, validate_report
from .style import Style, identify_style


def _append(line: str, dependency: str) -> str:
    body, marker, comment = line.partition("#")
    suffix = body[len(body.rstrip()):] if marker else ""
    if "|" in body:
        normal, _, order = body.partition("|")
        body = normal.rstrip() + " " + dependency + " |" + order
        suffix = ""
    else:
        body = body.rstrip() + " " + dependency
    return body + suffix + marker + comment


def _recipe_uses(document: Makefile, variable: str) -> bool:
    def uses(text: str) -> bool:
        names, opaque = reference_usage(text)
        return opaque or any(document.distance(f"variable:{ref}", f"variable:{variable}") is not None for ref in names)
    return any(uses(line) for line in document.text.splitlines() if line.startswith("\t")) or any(uses(rule.target_text) for rule in document.rules)


def _variable_for(document: Makefile, finding: Finding, report: Report) -> str | None:
    rule = finding.rule
    allowed = {
        target for item in report.missing if item.dependency == finding.dependency
        for target in item.rule.targets
    }
    candidates = []
    for name, variable in document.variables.items():
        distance = document.distance(f"target:{finding.target}", f"variable:{name}")
        affected = set(document.affected_targets(name))
        block = document.text.splitlines()[variable.line - 1:variable.end_line]
        if "|" in document.expand(f"$({name})") and not any("|" in line.partition("#")[0] for line in block):
            continue  # A separator hidden behind another reference has no safe insertion point here.
        if distance is not None and affected and affected <= allowed and not _recipe_uses(document, name):
            candidates.append((distance, variable.line, name))
    for _, _, name in sorted(candidates):
        # A substitution reference is not a safe place to append a literal.
        if any(f"$({name}:" in value for value in [rule.prerequisite_text, *(v.expression for v in document.variables.values())]):
            continue
        return name
    return None


def _check_explicit(
    before: Makefile, candidate: str, report: Report, *, accepted: set[str] | None = None,
) -> None:
    after = parse_makefile(candidate)
    if len(before.rules) != len(after.rules):
        raise RepairError("UNSUPPORTED_TARGET", "candidate changed rule structure")
    missing = [
        finding for finding in report.missing
        if not finding.rule.implicit
        and (accepted is None or finding.finding_id in accepted)
    ]
    allowed = {
        (target, finding.dependency)
        for finding in missing
        for target in finding.rule.targets
    }
    for old, new in zip(before.rules, after.rules):
        if old.targets != new.targets or old.order_only != new.order_only:
            raise RepairError("UNSUPPORTED_TARGET", "candidate changes unrelated targets or order-only dependencies")
        for target in old.targets:
            extras = [dep for dep in new.prerequisites if dep not in old.prerequisites]
            if any((target, dep) not in allowed for dep in extras):
                raise RepairError("UNSUPPORTED_TARGET", "shared variable would change an unrelated dependency")
            if any(dep not in new.prerequisites for dep in old.prerequisites):
                raise RepairError("UNSUPPORTED_TARGET", "candidate would remove an existing dependency")
    for finding in missing:
        new_rule = next(rule for rule in after.rules if rule.targets == finding.rule.targets)
        if finding.dependency not in new_rule.prerequisites:
            raise RepairError("UNSUPPORTED_TARGET", "variable edit does not reach the requested dependency")


def generate_patch(
    project_root: Path | str,
    report: Mapping | Path | str,
    *,
    makefile: str = "Makefile",
    commit: str | None = None,
    configuration_id: str | None = None,
    llm=None,
    collect_rejections: bool = False,
) -> PatchResult:
    """Create a candidate against HEAD. ``llm`` implements complete(prompt).

    A missing/invalid LLM only rejects complex implicit cases; explicit and
    simple compiler pattern rules need no network or model configuration.
    With ``collect_rejections``, target-level failures are returned in
    ``rejected_candidates`` instead of aborting the whole report.
    """
    source = read_source(project_root, makefile, commit=commit)
    document = parse_makefile(source.text)
    payload = report if isinstance(report, Mapping) else load_report(report)
    validated = validate_report(payload, source, document, configuration_id=configuration_id)
    newline = "\r\n" if "\r\n" in source.text else "\n"
    lines = source.text.splitlines()
    changes: list[Change] = []
    rejected: list[Rejection] = []
    accepted: set[str] = set()
    edits: set[tuple[int, str]] = set()
    for finding in validated.missing:
        rule = finding.rule
        if rule.implicit:
            continue
        try:
            style = identify_style(rule)
            if finding.dependency in rule.prerequisites:
                changes.append(Change(finding.finding_id, finding.target, finding.dependency, style.value, "already_declared", rule.line, "dependency already declared; no edit", changed=False))
                accepted.add(finding.finding_id)
                continue
            variable = _variable_for(document, finding, validated) if style in {Style.MACRO, Style.HYBRID} else None
            if style == Style.MACRO and variable is None:
                raise RepairError("UNSUPPORTED_TARGET", "no private variable can be edited while preserving macro style")
            first = document.variables[variable].line if variable else rule.line
            last = document.variables[variable].end_line if variable else rule.end_line
            location = next((line for line in range(first, last + 1) if "|" in lines[line - 1].partition("#")[0]), last)
            key = (location, finding.dependency)
            if key not in edits:
                lines[location - 1] = _append(lines[location - 1], finding.dependency)
                edits.add(key)
            affected = document.affected_targets(variable) if variable else finding.rule.targets
            reason = "append to a declaration variable; all affected targets request this dependency" if variable else "append to the existing target prerequisites; preserve variable references and existing dependencies"
            changes.append(Change(finding.finding_id, finding.target, finding.dependency, style.value, "variable" if variable else "target", location, reason, variable, affected))
            accepted.add(finding.finding_id)
        except RepairError as exc:
            if not collect_rejections:
                raise
            rejected.append(rejection_from_error(exc))
    candidate = newline.join(lines) + newline
    _check_explicit(document, candidate, validated, accepted=accepted)
    implicit = tuple(f for f in validated.missing if f.rule.implicit)
    if implicit:
        from .implicit import generate_implicit, is_simple_rule
        try:
            candidate, implicit_changes = generate_implicit(candidate, document, implicit, llm=llm)
            changes.extend(implicit_changes)
        except RepairError as exc:
            if not collect_rejections:
                raise
            simple = tuple(f for f in implicit if is_simple_rule(document, f.rule))
            complex_findings = tuple(f for f in implicit if f not in simple)
            if simple:
                try:
                    candidate, simple_changes = generate_implicit(candidate, document, simple, llm=None)
                    changes.extend(simple_changes)
                except RepairError as simple_exc:
                    rejected.extend(rejection_from_error(simple_exc) for _ in simple)
            rejected.extend(rejection_from_error(exc) for _ in complex_findings)
    # A callback must not invalidate the source snapshot while generating text.
    refreshed = read_source(source.root, source.name, commit=source.commit)
    if refreshed.sha256 != source.sha256:
        raise RepairError("STALE_REPORT", "source changed during candidate generation")
    # Git patches use LF even when the input Makefile uses CRLF. candidate_text
    # retains the original newline convention for consumers choosing to save it.
    diff = "".join(difflib.unified_diff(
        source.text.replace("\r\n", "\n").splitlines(keepends=True),
        candidate.replace("\r\n", "\n").splitlines(keepends=True),
        fromfile=f"a/{source.name}", tofile=f"b/{source.name}",
    ))
    patch = f"diff --git a/{source.name} b/{source.name}\n{diff}" if diff else ""
    plan = RepairPlan(source.commit, validated.configuration_id, source.name, source.sha256, tuple(changes), validated.ignored)
    return PatchResult(patch, candidate, plan, tuple(rejected))
