"""B07 内部字段的契约与运行时一致性。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from draft.service_runner import ValidationError as DraftValidationError
from draft.service_runner import validate_request as validate_draft
from mdfixer.service_runner import ValidationError as RepairValidationError
from mdfixer.service_runner import validate_request as validate_repair

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = json.loads((ROOT / "contracts" / "task.schema.json").read_text(encoding="utf-8"))
EXAMPLES = {
    "DRAFT": ROOT / "contracts" / "examples" / "draft" / "request.json",
    "REPAIR": ROOT / "contracts" / "examples" / "repair" / "request.json",
}


def input_branch(job_type: str) -> dict:
    for entry in SCHEMA["allOf"]:
        condition = entry.get("if", {}).get("properties", {}).get("job_type", {})
        if condition.get("const") == job_type:
            return entry["then"]["properties"]["input"]
    raise AssertionError(f"missing schema branch: {job_type}")


def load_example(job_type: str) -> dict:
    return json.loads(EXAMPLES[job_type].read_text(encoding="utf-8"))


def contract_accepts(job_type: str, payload: dict) -> bool:
    validator = Draft202012Validator(SCHEMA).evolve(schema=input_branch(job_type))
    return not list(validator.iter_errors(payload["input"]))


def runtime_accepts(job_type: str, payload: dict) -> bool:
    try:
        if job_type == "DRAFT":
            validate_draft(payload)
        else:
            validate_repair(payload)
    except (DraftValidationError, RepairValidationError):
        return False
    return True


def mutate(payload: dict, path: tuple[str, ...], value: object) -> None:
    target = payload["input"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


BLANK_FIELDS = (
    ("DRAFT", ("build", "command"), " "),
    ("DRAFT", ("build", "workdir"), ""),
    ("DRAFT", ("build", "workdir"), " "),
    ("DRAFT", ("verify", "command"), " "),
    ("REPAIR", ("makefile",), ""),
    ("REPAIR", ("makefile",), "   "),
    ("REPAIR", ("env", "build"), " "),
    ("REPAIR", ("env", "verify"), " "),
    ("REPAIR", ("recheck", "header"), " "),
    ("REPAIR", ("recheck", "macro"), " "),
    ("REPAIR", ("recheck", "target"), " "),
    ("REPAIR", ("recheck", "program"), " "),
)


@pytest.mark.parametrize("job_type,path,value", BLANK_FIELDS)
def test_blank_internal_fields_are_rejected_by_contract_and_runtime(
    job_type: str, path: tuple[str, ...], value: object
) -> None:
    payload = copy.deepcopy(load_example(job_type))
    mutate(payload, path, value)
    assert not contract_accepts(job_type, payload), f"contract accepted {job_type} {path}={value!r}"
    assert not runtime_accepts(job_type, payload), f"runtime accepted {job_type} {path}={value!r}"


@pytest.mark.parametrize("job_type", ("DRAFT", "REPAIR"))
def test_contract_examples_are_accepted_by_contract_and_runtime(job_type: str) -> None:
    payload = load_example(job_type)
    assert contract_accepts(job_type, payload)
    assert runtime_accepts(job_type, payload)
