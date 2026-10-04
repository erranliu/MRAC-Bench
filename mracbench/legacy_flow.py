"""Legacy generate/audit/repair workflow behind the shared workflow registry."""

import hashlib
import time

from mrac_contracts.providers import ProviderError

from .audit import Convergence, blocking_count, parse_audit, parse_spec
from .execution import Invoker
from .models import BenchError
from .protocol import render_prompt
from .repository import prepare_repository
from .runs import write_json


def run_legacy(config, adapter, store, case, protocol, result, maximum, timeout, started):
    invoke = None
    active_stage = "repository"
    try:
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
    except (BenchError, ProviderError) as exc:
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
