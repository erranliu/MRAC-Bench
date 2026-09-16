import hashlib
import time
from dataclasses import asdict
from pathlib import Path

from .audit import Convergence, blocking_count, parse_audit, parse_spec
from .cases import load_case, positive_int
from .models import AgentAdapter, AgentRequest, BenchError, RunConfig
from .protocol import load_protocol, render_prompt
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
    store.metadata["requested_config"] = {
        key: str(value) if isinstance(value, Path) else value
        for key, value in asdict(config).items()
    }
    store.save_metadata()
    store.checkpoint(result, active_stage)
    try:
        case = load_case(config.project_root, config.case_id)
        for name, content in case.snapshots.items():
            store.snapshot(name, content)
        protocol = load_protocol(config.project_root, case.protocol_id)
        for name, content in protocol.snapshots.items():
            store.snapshot(name, content)
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
            protocol_id=protocol.id,
            protocol_version=protocol.version,
            repository={"url": case.repository_url, "commit": case.commit},
            agent=result["agent"],
            effective_config={
                "max_audit_rounds": maximum,
                "agent_timeout_seconds": timeout,
                "required_clean_audits": 2,
                "model": config.model,
                "readonly": True,
                "ignore_user_config": True,
            },
        )
        store.save_metadata()
        active_stage = "repository"
        store.checkpoint(result, active_stage)
        with prepare_repository(case, config.workspace_dir, store.path) as repo:
            write_json(store.path / "input" / "repository-manifest.json", repo.baseline)
            store.metadata["repository"]["workspace"] = str(repo.path)
            store.save_metadata()

            def invoke(stage: str, prompt: str, audit_item: dict | None = None):
                nonlocal active_stage
                active_stage = stage
                raw = store.path / "raw" / stage
                raw.mkdir(exist_ok=False)
                before = repo.inspect()
                write_json(raw / "repository-before.json", before)
                if before["violation"]:
                    raise BenchError("PROTOCOL_VIOLATION", "Repository changed before invocation")
                (raw / "request.txt").write_text(prompt, encoding="utf-8")
                store.checkpoint(result, stage + ":started")
                execution = adapter.run(AgentRequest(prompt, repo.path, raw, timeout, config.model))
                # Adapters may stream these themselves. Stubs only return the content.
                for filename, content in (
                    ("stdout.txt", execution.stdout),
                    ("stderr.txt", execution.stderr),
                    ("final.txt", execution.final_text),
                ):
                    if not (raw / filename).exists():
                        (raw / filename).write_text(content, encoding="utf-8")
                write_json(
                    raw / "execution.json",
                    {
                        "started": execution.started,
                        "exit_code": execution.exit_code,
                        "duration_seconds": execution.duration_seconds,
                        "error_type": execution.error_type,
                        "error_message": execution.error_message,
                        "usage": execution.usage,
                        "metadata": execution.metadata,
                    },
                )
                if execution.started:
                    if audit_item is not None:
                        result["audit_rounds"] += 1
                        result["trajectory"].append(audit_item)
                    elif stage.startswith("repair-"):
                        result["repair_rounds"] += 1
                after = repo.inspect()
                write_json(raw / "repository-after.json", after)
                if after["violation"]:
                    error = BenchError(
                        "PROTOCOL_VIOLATION", "Agent changed the fixed repository snapshot"
                    )
                elif not execution.success:
                    error = BenchError(
                        execution.error_type or "AGENT_ERROR",
                        execution.error_message or "Agent invocation failed",
                    )
                else:
                    error = None
                if error:
                    if audit_item is not None and execution.started:
                        audit_item["status"] = error.kind
                    store.checkpoint(result, stage + ":failed")
                    raise error
                store.checkpoint(result, stage + ":completed")
                return execution.final_text

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
        result["error"] = {"type": exc.kind, "message": str(exc), "stage": active_stage}
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 -- persist unexpected terminal failures
        result["status"] = "INTERNAL_ERROR"
        result["error"] = {
            "type": "INTERNAL_ERROR",
            "message": str(exc) or type(exc).__name__,
            "stage": active_stage,
        }
    result["usage"]["wall_time_seconds"] = round(time.monotonic() - started, 3)
    store.finish(result)
    return store.path, result
