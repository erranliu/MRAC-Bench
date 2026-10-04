"""One dispatch boundary for workflow start, checkpoint inspection and continuation."""

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

from mrac_contracts.execution import ContractError, read_json

from .models import BenchError
from .revisions import revision_policy


def call(module, name, *args, **kwargs):
    return getattr(import_module(module, package=__package__), name)(*args, **kwargs)


def resume_exec(path, adapter, input_file, spec_file, operation):
    if spec_file:
        raise BenchError(
            "RESUME_ERROR", "Execution Spec is immutable; select a new Spec in a new run"
        )
    return call(
        ".exec_flow",
        "resume_exec",
        path,
        adapter,
        input_file,
        recover=operation in {"recover", "answer"},
    )


def resume_repository(path, adapter, input_file, spec_file, operation):
    return call(".repository_flow", "resume_repository_run", path, adapter, input_file, spec_file)


def resume_simple(path, adapter, input_file, spec_file, operation):
    if operation is not None and operation != "continue":
        raise ContractError("RECOVERY_UNSUPPORTED")
    if input_file or spec_file:
        raise BenchError("RESUME_ERROR", "Input/Spec import is supported only by v2")
    return call(".simple_flow", "resume_run", path, adapter)


def inspect_exec(path):
    store, result, case, _, config = call(".exec_flow", "load_exec", path)
    if result["flow"].get("candidate") is None:
        raise ContractError("No verified writable candidate checkpoint")
    call(".exec_flow", "verify_checkout", store, result, case, config)
    actions = ["recover"] if result["status"] not in {"CONVERGED", "ABORTED"} else []
    return result, resume_actions(result, actions)


def inspect_repository(path):
    _, result, _, _, _, _ = call(".repository_flow", "load_session", path)
    actions = (
        ["recover"]
        if result["status"]
        not in {
            "CONVERGED",
            "NON_CONVERGED",
            "ABORTED",
        }
        else []
    )
    return result, resume_actions(result, actions)


def resume_actions(result, actions):
    if result["status"] == "PAUSED":
        return ["continue"]
    if result["status"] == "NEEDS_INPUT":
        return ["answer"]
    return actions


def inspect_simple(path):
    result = read_json(path / "result.json")
    actions = (
        ["continue"]
        if (
            result.get("protocol_id") == "spec-flow-simple-v1"
            and result.get("flow", {}).get("schema_version") == 3
            and revision_policy(
                "spec-init-freeze", result.get("protocol_version", 0)
            ).managed_continue
            and result["status"] == "PAUSED"
        )
        else []
    )
    return result, actions


def inspect_legacy(path):
    return read_json(path / "result.json"), []


@dataclass(frozen=True)
class Workflow:
    name: str
    protocol_id: str
    module: str
    start_function: str
    continuation: Callable | None
    managed_inspection: Callable
    checkpoint: str | None = None
    cli_inspection: str | None = None
    report_function: str | None = None
    accepts_maximum: bool = True

    def start(self, config, adapter, store, case, protocol, result, maximum, timeout, started):
        args = [config, adapter, store, case, protocol, result]
        if self.accepts_maximum:
            args.append(maximum)
        return call(self.module, self.start_function, *args, timeout, started)

    def inspect(self, path, *, abort_reason=None):
        if self.cli_inspection is None:
            raise BenchError("RESUME_ERROR", "Status/report/abort requires a v2 or Exec run")
        return call(self.module, self.cli_inspection, path, abort_reason=abort_reason)

    def report(self, path, result):
        if self.report_function:
            # Historical reports must be rendered in memory without rewriting evidence.
            return call(self.module, self.report_function, result)
        report = path / "run-report.md"
        return report.read_text(encoding="utf-8") if report.exists() else ""

    def resume(self, path, adapter, input_file=None, spec_file=None, *, operation=None):
        if self.continuation is None:
            if operation is not None:
                raise ContractError("RECOVERY_UNSUPPORTED")
            raise BenchError("RESUME_ERROR", "This workflow does not support resume")
        return self.continuation(path, adapter, input_file, spec_file, operation)


FLOW_REGISTRY = {
    "exec-mrac": Workflow(
        "exec-mrac",
        "exec-mrac-v1",
        ".exec_flow",
        "run_exec",
        resume_exec,
        inspect_exec,
        checkpoint="exec-state.json",
        cli_inspection="inspect_exec",
        accepts_maximum=False,
    ),
    "repository-spec-freeze": Workflow(
        "repository-spec-freeze",
        "spec-mrac-v2",
        ".repository_flow",
        "run_repository_flow",
        resume_repository,
        inspect_repository,
        checkpoint="repository-state.json",
        cli_inspection="inspect_repository_run",
        report_function="render_report",
    ),
    "spec-init-freeze": Workflow(
        "spec-init-freeze",
        "spec-flow-simple-v1",
        ".simple_flow",
        "run_simple",
        resume_simple,
        inspect_simple,
    ),
    "generate-audit-repair": Workflow(
        "generate-audit-repair",
        "spec-mrac-v1",
        ".legacy_flow",
        "run_legacy",
        None,
        inspect_legacy,
    ),
}


def saved_workflow(path: Path) -> Workflow:
    # Canonical checkpoints retain precedence over projections and mutable metadata.
    for workflow in FLOW_REGISTRY.values():
        if workflow.checkpoint and (path / workflow.checkpoint).is_file():
            return workflow
    if not path.is_dir():
        raise BenchError("RESUME_ERROR", "Run directory does not exist")
    try:
        result = read_json(path / "result.json")
        if not isinstance(result, dict):
            raise TypeError("Saved result must be an object")
        if result.get("workflow") in FLOW_REGISTRY:
            return FLOW_REGISTRY[result["workflow"]]
        return next(
            (
                flow
                for flow in FLOW_REGISTRY.values()
                if flow.protocol_id == result.get("protocol_id")
            ),
            FLOW_REGISTRY["generate-audit-repair"],
        )
    except (OSError, ValueError, TypeError) as exc:
        raise BenchError("RESUME_ERROR", f"Cannot identify saved workflow: {exc}") from exc
