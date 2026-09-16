import hashlib
import time
from dataclasses import asdict
from pathlib import Path

from .audit import Convergence, blocking_count, parse_audit, parse_spec
from .cases import load_case, positive_int
from .execution import Invoker
from .models import AgentAdapter, BenchError, RunConfig
from .protocol import DEFAULT_PROTOCOL_ID, load_protocol, render_prompt
from .repository import prepare_repository
from .runs import RunStore, write_json


def run_case(config: RunConfig, adapter: AgentAdapter) -> tuple[Path, dict]:
    started = time.monotonic()
    store = RunStore(config.runs_dir, config.case_id, config.project_root)
    result = {
        "run_id": store.run_id,
        "case_id": config.case_id,
        "case_version": None,
        "protocol_id": None,
        "protocol_version": None,
        "agent": {"type": adapter.agent_type, "model": config.model, "version": None},
        "status": "RUNNING",
        "convergence_round": None,
        "audit_rounds": 0,
        "repair_rounds": 0,
        "trajectory": [],
        "protocol_violation": False,
        "usage": {"wall_time_seconds": None, "tokens": None, "cost": None},
        "error": None,
        "final_artifact": None,
    }
    active_stage = "initialize"
    invoke = None
    store.metadata["requested_config"] = {
        key: str(value.resolve()) if isinstance(value, Path) else value
        for key, value in asdict(config).items()
    }
    store.save_metadata()
    store.checkpoint(result, active_stage)
    try:
        case = load_case(config.project_root, config.case_id)
        for name, content in case.snapshots.items():
            store.snapshot(name, content)
        protocol = load_protocol(
            config.project_root,
            config.protocol_id if config.protocol_id is not None else DEFAULT_PROTOCOL_ID,
        )
        for name, content in protocol.snapshots.items():
            store.snapshot(name, content)
        if protocol.workflow == "exec-mrac":
            if config.spec_file is None:
                raise BenchError("CASE_ERROR", "exec-mrac requires an explicit --spec-file")
            if config.max_rounds is not None and (
                type(config.max_rounds) is not int or config.max_rounds != 6
            ):
                raise BenchError(
                    "CASE_ERROR", "exec-mrac runs in six-audit batches; resume adds six"
                )
            maximum = 6
        elif config.spec_file is not None:
            raise BenchError("CASE_ERROR", "run --spec-file is supported only by exec-mrac")
        elif protocol.workflow == "repository-spec-freeze":
            maximum = (
                config.max_rounds if config.max_rounds is not None else protocol.max_audit_rounds
            )
            if maximum is not None:
                maximum = positive_int(maximum, "max_audit_rounds")
        else:
            maximum = positive_int(
                config.max_rounds
                if config.max_rounds is not None
                else (
                    case.max_audit_rounds
                    if case.max_audit_rounds is not None
                    else protocol.max_audit_rounds
                ),
                "max_audit_rounds",
            )
        timeout = positive_int(
            config.timeout_seconds if config.timeout_seconds is not None else case.timeout_seconds,
            "agent_timeout_seconds",
        )
        result.update(
            case_version=case.version, protocol_id=protocol.id, protocol_version=protocol.version
        )
        result["agent"]["version"] = adapter.version()
        store.metadata.update(
            case_id=case.id,
            case_version=case.version,
            protocol_selection="explicit" if config.protocol_id is not None else "default",
            protocol_id=protocol.id,
            protocol_version=protocol.version,
            repository={"url": case.repository_url, "commit": case.commit},
            agent=result["agent"],
            effective_config={
                "max_audit_rounds": maximum,
                "agent_timeout_seconds": timeout,
                "required_clean_audits": 2,
                "model": config.model,
                "reasoning_effort": config.reasoning_effort,
                "readonly": protocol.workflow != "exec-mrac",
                "ignore_user_config": True,
            },
        )
        store.save_metadata()
        if protocol.workflow == "exec-mrac":
            from .exec_flow import run_exec

            return run_exec(config, adapter, store, case, protocol, result, timeout, started)
        if protocol.workflow == "repository-spec-freeze":
            from .repository_flow import run_repository_flow

            return run_repository_flow(
                config, adapter, store, case, protocol, result, maximum, timeout, started
            )
        if protocol.workflow == "spec-init-freeze":
            from .simple_flow import run_simple

            return run_simple(
                config, adapter, store, case, protocol, result, maximum, timeout, started
            )
        active_stage = "repository"
        store.checkpoint(result, active_stage)
        with prepare_repository(case, config.workspace_dir, store.path) as repo:
            write_json(store.path / "input" / "repository-manifest.json", repo.baseline)
            store.metadata["repository"]["workspace"] = str(repo.path)
            store.save_metadata()

            invoke = Invoker(
                store, result, repo, adapter, config.model, timeout, config.reasoning_effort
            )

            spec = parse_spec(
                invoke(
                    "generate", render_prompt(protocol.prompts["generate"], case.task, repo.path)
                )
            )
            current = store.artifact("spec.initial.md", spec)
            result["final_artifact"] = current
            store.checkpoint(result, "generated")
            convergence = Convergence()
            for round_number in range(1, maximum + 1):
                stage = f"audit-{round_number:02d}"
                item = {
                    "audit_round": round_number,
                    "artifact": current,
                    "artifact_sha256": hashlib.sha256(spec.encode("utf-8")).hexdigest(),
                    "blocking_issue_count": None,
                    "status": "RUNNING",
                    "repair_artifact": None,
                }
                text = invoke(
                    stage,
                    render_prompt(protocol.prompts["audit"], case.task, repo.path, spec),
                    item,
                )
                try:
                    audit = parse_audit(text)
                except BenchError as exc:
                    item["status"] = exc.kind
                    raise
                write_json(store.path / "audits" / f"{stage}.json", audit)
                count = blocking_count(audit)
                item.update(blocking_issue_count=count, status=audit["status"])
                converged = convergence.observe(count)
                item["clean_streak"] = convergence.clean_streak
                store.checkpoint(result, stage + ":parsed")
                if converged:
                    result.update(status="CONVERGED", convergence_round=round_number)
                    break
                if round_number == maximum:
                    result["status"] = "NON_CONVERGED"
                    break
                if count:
                    repair_number = result["repair_rounds"] + 1
                    text = invoke(
                        f"repair-{repair_number:02d}",
                        render_prompt(
                            protocol.prompts["repair"], case.task, repo.path, spec, audit
                        ),
                    )
                    spec = parse_spec(text)
                    current = store.artifact(f"spec.round-{repair_number:02d}.md", spec)
                    item["repair_artifact"] = current
                    result["final_artifact"] = current
                    store.checkpoint(result, f"repair-{repair_number:02d}:saved")
    except BenchError as exc:
        result["status"] = exc.kind
        result["protocol_violation"] = exc.kind == "PROTOCOL_VIOLATION"
        result["error"] = {
            "type": exc.kind,
            "message": str(exc),
            "stage": invoke.stage if invoke else active_stage,
        }
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 -- persist unexpected terminal failures
        result["status"] = "INTERNAL_ERROR"
        result["error"] = {
            "type": "INTERNAL_ERROR",
            "message": str(exc) or type(exc).__name__,
            "stage": invoke.stage if invoke else active_stage,
        }
    result["usage"]["wall_time_seconds"] = round(time.monotonic() - started, 3)
    store.finish(result)
    return store.path, result
