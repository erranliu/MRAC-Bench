import json
from pathlib import Path

import pytest

from mrac_contracts.execution import ContractError
from mracbench.cases import load_case
from mracbench.configuration import resolve_limits
from mracbench.machine import validate
from mracbench.models import BenchError, Case, ProtocolDefinition
from mracbench.protocol import load_protocol
from mracbench.workflows import FLOW_REGISTRY, saved_workflow


@pytest.mark.parametrize(
    "workflow,case_cap,protocol_cap,override,expected",
    [
        ("generate-audit-repair", 5, 8, None, 5),
        ("spec-init-freeze", 5, 8, None, 5),
        ("spec-init-freeze", None, 8, None, 8),
        ("spec-init-freeze", 5, 8, 12, 12),
        ("repository-spec-freeze", 5, None, None, None),
        ("repository-spec-freeze", 5, 8, None, 8),
        ("repository-spec-freeze", 5, None, 12, 12),
        ("exec-mrac", 5, 6, None, 6),
    ],
)
def test_limit_precedence(workflow, case_cap, protocol_cap, override, expected):
    case = Case("case", 1, "repository", "base", "task", case_cap, 90, {})
    protocol = ProtocolDefinition(
        "protocol",
        15 if workflow == "spec-init-freeze" else 4,
        protocol_cap,
        {},
        {},
        workflow=workflow,
    )
    limits = resolve_limits(
        case,
        protocol,
        max_rounds=override,
        spec_file=Path("spec.md") if workflow == "exec-mrac" else None,
    )
    assert limits.max_audit_rounds == expected
    assert limits.timeout_seconds == 90
    assert (
        resolve_limits(
            case,
            protocol,
            max_rounds=override,
            timeout_seconds=30,
            spec_file=Path("spec.md") if workflow == "exec-mrac" else None,
        ).timeout_seconds
        == 30
    )


@pytest.mark.parametrize(
    "protocol_id", ["spec-mrac-v1", "spec-mrac-v2", "spec-flow-simple-v1", "exec-mrac-v1"]
)
def test_managed_and_standalone_resolution_match(current_project, protocol_id):
    spec = current_project / "execution-spec.md"
    if protocol_id == "exec-mrac-v1":
        spec.write_text("# Execution Spec\nSet value to 2.", encoding="utf-8")
    settings = {
        "case_id": "sample",
        "protocol_id": protocol_id,
        "model": "test",
        "timeout_seconds": 37,
    }
    case = load_case(current_project, "sample")
    protocol = load_protocol(current_project, protocol_id)
    direct = resolve_limits(
        case, protocol, timeout_seconds=37, spec_file=spec if spec.exists() else None
    )
    managed = validate(current_project, settings)
    assert managed["max_rounds"] == direct.max_audit_rounds
    assert managed["timeout_seconds"] == direct.timeout_seconds
    assert managed["repository_access"] == ("read_only" if direct.readonly else "writable")


@pytest.mark.parametrize("cap", [0, -1, True, 6.0, 5, 7])
def test_exec_rejects_noninteger_or_non_six_budget_at_both_boundaries(project, cap):
    spec = project / "execution-spec.md"
    spec.write_text("# Execution Spec")
    with pytest.raises(BenchError):
        resolve_limits(
            load_case(project, "sample"),
            load_protocol(project, "exec-mrac-v1"),
            max_rounds=cap,
            spec_file=spec,
        )
    with pytest.raises(ContractError):
        validate(project, {"case_id": "sample", "protocol_id": "exec-mrac-v1", "max_rounds": cap})


def test_canonical_checkpoint_precedence_survives_corrupt_projection(tmp_path):
    (tmp_path / "result.json").write_text("invalid projection")
    (tmp_path / "repository-state.json").write_text("invalid repository checkpoint")
    assert saved_workflow(tmp_path).name == "repository-spec-freeze"
    (tmp_path / "exec-state.json").write_text("invalid exec checkpoint")
    assert saved_workflow(tmp_path).name == "exec-mrac"
    # Selecting a profile does not bypass canonical checkpoint validation.
    with pytest.raises(BenchError):
        saved_workflow(tmp_path).managed_inspection(tmp_path)


@pytest.mark.parametrize(
    "operation,recover", [(None, False), ("continue", False), ("recover", True), ("answer", True)]
)
def test_exec_dispatch_preserves_explicit_extension_vs_recovery(
    tmp_path, monkeypatch, operation, recover
):
    calls = []
    monkeypatch.setattr(
        "mracbench.exec_flow.resume_exec",
        lambda path, adapter, input_file, **kwargs: calls.append(kwargs),
    )
    workflow = FLOW_REGISTRY["exec-mrac"]
    workflow.resume(tmp_path, object(), operation=operation)
    assert calls == [{"recover": recover}]
    with pytest.raises(BenchError, match="immutable"):
        workflow.resume(
            tmp_path, object(), spec_file=tmp_path / "replacement.md", operation=operation
        )
    assert len(calls) == 1


def test_saved_simple_dispatch_uses_result_identity_and_rejects_recovery(tmp_path):
    result = {
        "protocol_id": "spec-flow-simple-v1",
        "protocol_version": 9,
        "status": "PAUSED",
        "flow": {"schema_version": 3},
    }
    (tmp_path / "result.json").write_text(json.dumps(result))
    workflow = saved_workflow(tmp_path)
    assert workflow.managed_inspection(tmp_path) == (result, ["continue"])
    with pytest.raises(ContractError, match="RECOVERY_UNSUPPORTED"):
        workflow.resume(tmp_path, object(), operation="recover")
    with pytest.raises(BenchError, match="Input/Spec"):
        workflow.resume(tmp_path, object(), input_file=tmp_path / "answer.md")


def test_registry_covers_all_declared_workflows():
    from mracbench.protocol import WORKFLOWS

    assert FLOW_REGISTRY.keys() == WORKFLOWS.keys()


@pytest.mark.parametrize("result", [None, "invalid JSON", "[]"])
def test_missing_or_corrupt_simple_projection_remains_a_reviewable_cli_error(
    tmp_path, capsys, result
):
    from mracbench.cli import main

    (tmp_path / "run.yaml").write_text("{}")
    if result is not None:
        (tmp_path / "result.json").write_text(result)
    assert main(["resume", "--run-dir", str(tmp_path)]) == 2
    assert "RESUME_ERROR" in capsys.readouterr().out
