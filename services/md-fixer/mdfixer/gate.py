"""E10 transaction gate: sandbox, build, recheck, accept or roll back."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .accept import accept_candidate
from .build import BuildResult, ProgramResult, clean_build, run_program
from .guard import RepairError, project_file, read_source
from .makefile import parse_makefile
from .patch import generate_patch
from .recheck import DeclarationCheck, IncrementalResult, check_declarations, run_incremental
from .report import load_report, validate_report
from .result import (
    CHECKS,
    SCENARIOS,
    gate_payload,
    scenario_passed,
    validation_payload,
    write_json,
    write_results_csv,
    write_scenario_artifacts,
)
from .rollback import rollback_source, source_unchanged
from .sandbox import (
    ApplyResult,
    apply_candidate,
    create_sandbox,
    remove_sandbox,
)

INVALID_PATCH = """diff --git a/Makefile b/Makefile
--- a/Makefile
+++ b/Makefile
@@ -1,1 +1,1 @@
-this declaration does not exist
+invalid replacement
"""


@dataclass(frozen=True)
class TransactionResult:
    scenario: str
    validation: dict
    build_log: str = ""
    incremental_log: str = ""
    recheck: dict | None = None
    sandbox: str | None = None

    def to_payload(self) -> dict:
        return {
            "scenario": self.scenario,
            "validation": self.validation,
            "recheck": self.recheck,
            "sandbox": self.sandbox,
        }


@dataclass(frozen=True)
class GateResult:
    passed: bool
    rows: int
    scenarios: tuple[dict, ...]
    output_dir: str | None = None

    def to_payload(self) -> dict:
        return gate_payload(passed=self.passed, rows=self.rows, scenarios=list(self.scenarios))


def _payload(report: Mapping | Path | str) -> Mapping:
    return report if isinstance(report, Mapping) else load_report(report)


def _redundant_edges(payload: Mapping) -> tuple[tuple[str, str], ...]:
    findings = payload.get("findings")
    if not isinstance(findings, list):
        return ()
    return tuple(
        (item["target"], item["dependency"])
        for item in findings
        if isinstance(item, Mapping)
        and item.get("type") == "REDUNDANT"
        and isinstance(item.get("target"), str)
        and isinstance(item.get("dependency"), str)
    )


def _read_sandbox_makefile(sandbox: Path, makefile: str) -> str:
    path = project_file(sandbox, makefile)
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise RepairError("MISSING_INPUT", "cannot read sandbox Makefile") from exc


def _failure_reason(
    apply: ApplyResult | None,
    build: BuildResult | None,
    program: ProgramResult | None,
    incremental: IncrementalResult | None,
    declaration: DeclarationCheck | None,
) -> str:
    if apply is None:
        return "candidate patch was not applied"
    if not apply.ok:
        return apply.error or "candidate patch was rejected"
    if build is not None and not build.ok:
        return (build.build.stderr or build.build.stdout).strip() or "clean build failed"
    if program is not None and not program.ok:
        return program.error or "program output did not match"
    if incremental is not None and not incremental.ok:
        return incremental.error or "incremental rebuild failed"
    if declaration is not None and not declaration.ok:
        if declaration.error:
            return declaration.error
        if declaration.missing_remaining:
            return "MISSING edges remain: " + ", ".join(
                f"{target} -> {dependency}" for target, dependency in declaration.missing_remaining
            )
        if declaration.redundant_missing:
            return "REDUNDANT edges were removed"
    return "one or more validation checks failed"


def run_transaction(
    project_root: Path | str,
    report: Mapping | Path | str,
    *,
    scenario: str = "accept",
    makefile: str = "Makefile",
    build_target: str = "app",
    clean_target: str = "clean",
    program: str = "app",
    expected_output: str = "1",
    header: str = "config.h",
    macro: str = "VALUE",
    old_value: str = "1",
    new_value: str = "2",
    patch: str | None = None,
    commit: str | None = None,
    configuration_id: str | None = None,
    timeout: int = 120,
    env: dict[str, str] | None = None,
    keep_sandbox: bool = False,
    sandbox_dir: Path | str | None = None,
) -> TransactionResult:
    """Run one candidate transaction. Only a fully passing candidate is written back."""
    project = Path(project_root).resolve()
    source = read_source(project, makefile, commit=commit)
    try:
        original = source.path.read_bytes()
    except OSError as exc:
        raise RepairError("MISSING_INPUT", "cannot read source Makefile") from exc
    payload = _payload(report)
    document = parse_makefile(source.text)
    validated = validate_report(payload, source, document, configuration_id=configuration_id)
    redundant = _redundant_edges(payload)

    candidate_text: str | None = None
    plan: dict | None = None
    if patch is None:
        generated = generate_patch(
            project,
            payload,
            makefile=makefile,
            commit=source.commit,
            configuration_id=configuration_id,
        )
        git_patch = generated.git_patch
        candidate_text = generated.candidate_text
        plan = generated.plan.to_payload()
    else:
        git_patch = patch

    sandbox = create_sandbox(project, sandbox_dir)
    checks = {name: False for name in CHECKS}
    applied: ApplyResult | None = None
    build: BuildResult | None = None
    program_result: ProgramResult | None = None
    incremental: IncrementalResult | None = None
    declaration: DeclarationCheck | None = None
    accepted = False
    error: str | None = None
    source_changed = False
    rollback = False
    try:
        applied = apply_candidate(sandbox, git_patch)
        checks["patch_applies"] = applied.ok
        if applied.ok:
            build = clean_build(
                sandbox,
                build_target,
                clean_target=clean_target,
                timeout=timeout,
                env=env,
            )
            checks["build"] = build.ok
            if build.ok:
                program_result = run_program(
                    sandbox, program, expected_output, timeout=timeout, env=env
                )
                checks["test"] = program_result.ok
                incremental = run_incremental(
                    sandbox,
                    header=header,
                    macro=macro,
                    old_value=old_value,
                    new_value=new_value,
                    target=build_target,
                    program=program,
                    timeout=timeout,
                    env=env,
                )
                checks["header_change"] = incremental.header_change
                declaration = check_declarations(
                    _read_sandbox_makefile(sandbox, makefile),
                    validated.missing,
                    redundant,
                    sandbox=sandbox,
                )
                checks["no_missing"] = declaration.ok
        accepted = all(checks.values())
        if accepted:
            try:
                accept_candidate(
                    project,
                    candidate_text or "",
                    makefile=makefile,
                    expected_sha256=source.sha256,
                )
                source_changed = True
                rollback = False
            except RepairError as exc:
                accepted = False
                error = str(exc)
                restored = rollback_source(project, original, makefile)
                rollback = True
                source_changed = not restored.source_unchanged
        if not accepted:
            restored = rollback_source(project, original, makefile)
            rollback = True
            source_changed = not restored.source_unchanged
            error = error or _failure_reason(applied, build, program_result, incremental, declaration)
    finally:
        retained = str(sandbox) if keep_sandbox else None
        if not keep_sandbox:
            remove_sandbox(sandbox)

    recheck = {
        "incremental": incremental.to_payload() if incremental is not None else None,
        "declarations": declaration.to_payload() if declaration is not None else None,
    }
    validation = validation_payload(
        accepted=accepted,
        checks=checks,
        source_unchanged=not source_changed,
        rollback=rollback,
        error=error,
        details={
            "git_patch": git_patch,
            "plan": plan,
            "apply": applied.to_payload() if applied is not None else {},
            "recheck": recheck,
        },
    )
    return TransactionResult(
        scenario=scenario,
        validation=validation,
        build_log=build.log if build is not None else "",
        incremental_log=incremental.log if incremental is not None else "",
        recheck=recheck,
        sandbox=retained,
    )


def _row(scenario: str, transaction: TransactionResult) -> dict:
    validation = transaction.validation
    checks = validation["checks"]
    return {
        "scenario": scenario,
        **{name: checks[name] for name in CHECKS},
        "accepted": validation["accepted"],
        "source_unchanged": validation["source_unchanged"],
        "rollback": validation["rollback"],
        "passed": scenario_passed(scenario, validation),
        "error": validation.get("error") or "",
    }


def run_gate(
    project_root: Path | str,
    report: Mapping | Path | str,
    *,
    output_dir: Path | str | None = None,
    makefile: str = "Makefile",
    build_target: str = "app",
    clean_target: str = "clean",
    program: str = "app",
    expected_output: str = "1",
    reject_expected_output: str = "99",
    header: str = "config.h",
    macro: str = "VALUE",
    old_value: str = "1",
    new_value: str = "2",
    commit: str | None = None,
    configuration_id: str | None = None,
    timeout: int = 120,
    env: dict[str, str] | None = None,
    keep_sandbox: bool = False,
) -> GateResult:
    """Run reject-test, reject-patch and accept scenarios on independent copies."""
    project = Path(project_root).resolve()
    output = Path(output_dir).resolve() if output_dir is not None else None
    if output is not None:
        output.mkdir(parents=True, exist_ok=True)
    transactions: dict[str, TransactionResult] = {}
    # Rejections run on copies so the accept case is the only source mutation.
    for scenario in ("reject-test", "reject-patch", "accept"):
        source = project
        copied = False
        if scenario != "accept":
            source = create_sandbox(project)
            copied = True
        try:
            transactions[scenario] = run_transaction(
                source,
                report,
                scenario=scenario,
                makefile=makefile,
                build_target=build_target,
                clean_target=clean_target,
                program=program,
                expected_output=reject_expected_output if scenario == "reject-test" else expected_output,
                header=header,
                macro=macro,
                old_value=old_value,
                new_value=new_value,
                patch=INVALID_PATCH if scenario == "reject-patch" else None,
                commit=commit,
                configuration_id=configuration_id,
                timeout=timeout,
                env=env,
                keep_sandbox=keep_sandbox,
            )
        finally:
            if copied:
                remove_sandbox(source)

    rows = [_row(scenario, transactions[scenario]) for scenario in SCENARIOS]
    passed = all(row["passed"] for row in rows)
    scenarios = [
        {
            "scenario": scenario,
            "passed": scenario_passed(scenario, transactions[scenario].validation),
            "validation": transactions[scenario].validation,
        }
        for scenario in SCENARIOS
    ]
    if output is not None:
        for scenario in SCENARIOS:
            transaction = transactions[scenario]
            write_scenario_artifacts(
                output,
                scenario,
                validation=transaction.validation,
                build_log=transaction.build_log,
                incremental_log=transaction.incremental_log,
                recheck=transaction.recheck,
            )
        write_json(output / "gate.json", gate_payload(passed=passed, rows=len(rows), scenarios=scenarios))
        write_results_csv(output / "results.csv", rows)
    return GateResult(passed, len(rows), tuple(scenarios), str(output) if output else None)
