"""Two explicit Spec stages extracted from MRAC-Flow Simple (no product workflow)."""

import json
import time
from contextlib import nullcontext
from pathlib import Path

from .audit import parse_spec
from .cases import case_from_snapshots, load_yaml, positive_int
from .evidence import Evidence, digest, run_lock
from .execution import Invoker
from .models import BenchError, RunConfig
from .protocol import protocol_from_snapshots, render_simple_prompt
from .providers import check_adapter
from .repository import prepare_repository
from .runs import RunStore, utc_now
from .simple_audit import assign_ids, parse_findings, parse_repair, parse_review


def save_evidence(store, evidence, name, data):
    path = evidence.path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    evidence.record(name)


def baseline_inputs(case, repo, spec):
    return {
        "source_spec": case.task,
        "source_spec_sha256": digest(case.snapshots["task.md"]),
        "current_spec": spec,
        "repository_path": str(repo.path),
        "fixed_repository_head": case.commit,
        "related_specs": case.related_specs,
    }


def run_simple(config, adapter, store, case, protocol, result, maximum, timeout, started):
    with run_lock(store.path):
        store.snapshot(
            "execution-config.json",
            json.dumps(
                {
                    key: store.metadata[key]
                    for key in (
                        "requested_config",
                        "effective_config",
                        "agent",
                        "protocol_id",
                        "protocol_version",
                    )
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
        )
        result.update(
            review_rounds=0,
            workflow=protocol.workflow,
            frozen_spec_sha256=None,
            clean_audit_ids=[],
            deferred_p3=[],
            terminal_reason=None,
            flow={
                "schema_version": 1,
                "phase": "spec-init",
                "failure_streak": 0,
                "clean": [],
                "pending_fix": None,
                "resume_count": 0,
            },
        )
        evidence = Evidence(store.path)
        for name in store.metadata["input_sha256"]:
            evidence.record("input/" + name)
        # Preserve source bytes (including BOM and line endings) before any model invocation.
        relative = "artifacts/spec.initial.md"
        with evidence.path(relative).open("xb") as stream:
            stream.write(case.snapshots["task.md"])
        evidence.record(relative)
        result["final_artifact"] = relative
        result["flow"]["artifact_sha256"] = evidence.known[relative]
        store.checkpoint(result, "copied")
        return execute_simple(
            config, adapter, store, case, protocol, result, maximum, timeout, evidence, started
        )


def execute_simple(
    config,
    adapter,
    store,
    case,
    protocol,
    result,
    maximum,
    timeout,
    evidence,
    started,
    prepared_repo=None,
):
    invoke = None
    try:
        repository = (
            nullcontext(prepared_repo)
            if prepared_repo is not None
            else prepare_repository(case, config.workspace_dir, store.path)
        )
        with repository as repo:
            baseline_name = "input/repository-manifest.json"
            if baseline_name in evidence.known:
                expected = json.loads(evidence.path(baseline_name).read_text(encoding="utf-8"))
                if repo.baseline != expected:
                    raise BenchError("PROTOCOL_VIOLATION", "Repository differs from original run")
            else:
                save_evidence(store, evidence, baseline_name, repo.baseline)
            store.metadata["repository"]["workspace"] = str(repo.path)
            store.save_metadata()
            invoke = Invoker(
                store,
                result,
                repo,
                adapter,
                config.model,
                timeout,
                config.reasoning_effort,
                evidence.check,
                evidence.capture,
            )
            drive_simple(store, case, protocol, result, maximum, repo, invoke, evidence)
            evidence.check()
    except BenchError as exc:
        result["status"] = exc.kind
        result["protocol_violation"] = exc.kind == "PROTOCOL_VIOLATION"
        result["error"] = {
            "type": exc.kind,
            "message": str(exc),
            "stage": invoke.stage if invoke else "repository",
        }
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
        result["status"] = "INTERNAL_ERROR"
        result["error"] = {
            "type": "INTERNAL_ERROR",
            "message": str(exc) or type(exc).__name__,
            "stage": invoke.stage if invoke else "repository",
        }
    result["usage"]["wall_time_seconds"] = round(
        (result["usage"]["wall_time_seconds"] or 0) + time.monotonic() - started, 3
    )
    store.metadata["evidence_sha256"] = evidence.known
    store.finish(result)
    # A paused checkpoint is immutable until a successful, explicit resume takes its lock.
    store.metadata["result_sha256"] = digest((store.path / "result.json").read_bytes())
    store.save_metadata()
    return store.path, result


def drive_simple(store, case, protocol, result, maximum, repo, invoke, evidence):
    flow = result["flow"]
    while True:
        evidence.check()
        current = result["final_artifact"]
        spec_bytes = evidence.path(current).read_bytes()
        if digest(spec_bytes) != flow["artifact_sha256"]:
            raise BenchError("PROTOCOL_VIOLATION", "Active Spec hash changed")
        spec = spec_bytes.decode("utf-8-sig")
        if result["audit_rounds"] >= maximum:
            result.update(status="NON_CONVERGED", terminal_reason="Total audit budget exhausted")
            return
        pending = flow["pending_fix"]
        if pending:
            number = result["repair_rounds"] + 1
            stage = f"repair-{number:02d}"
            inputs = baseline_inputs(case, repo, spec)
            inputs.update(audit_id=pending["audit_id"], accepted_findings=pending["accepted"])
            prompt_stage = "repair-init" if pending["kind"] == "spec-init" else "repair-freeze"
            repair = parse_repair(
                invoke(stage, render_simple_prompt(protocol.prompts[prompt_stage], inputs)),
                pending["audit_id"],
                pending["accepted"],
            )
            save_evidence(store, evidence, f"repairs/{stage}.json", repair)
            if repair["disposition"] == "block":
                result.update(status="BLOCKED", terminal_reason=repair["reason"])
                return
            replacement = parse_spec(repair["spec"])
            if replacement.encode("utf-8") == spec_bytes:
                raise BenchError("PARSE_ERROR", "Accepted repair did not change Spec bytes")
            new_artifact = store.artifact(f"spec.round-{number:02d}.md", replacement)
            evidence.record(new_artifact)
            result["trajectory"][-1]["repair_artifact"] = new_artifact
            result["final_artifact"] = new_artifact
            flow.update(
                phase="spec-freeze-loop",
                pending_fix=None,
                clean=[],
                artifact_sha256=evidence.known[new_artifact],
            )
            store.checkpoint(result, stage + ":saved")
            continue

        kind = flow["phase"]
        number = result["audit_rounds"] + 1
        stage = f"{kind}-{number:02d}"
        audit_id = f"{store.run_id}-{stage}"
        spec_only = kind == "spec-freeze-loop"
        inputs = {"current_spec": spec} if spec_only else baseline_inputs(case, repo, spec)
        inputs.update(audit_id=audit_id, spec_sha256=flow["artifact_sha256"])
        workspace = None
        if spec_only:
            workspace = store.path / "audit-workspaces" / stage
            workspace.mkdir(parents=True, exist_ok=False)
            evidence.empty_workspaces.append(workspace)
        item = {
            "audit_round": number,
            "audit_id": audit_id,
            "audit_kind": kind,
            "artifact": current,
            "artifact_sha256": flow["artifact_sha256"],
            "blocking_issue_count": None,
            "status": "RUNNING",
            "repair_artifact": None,
        }
        text = invoke(
            stage,
            render_simple_prompt(protocol.prompts[kind], inputs, spec_only=spec_only),
            item,
            workspace=workspace,
        )
        try:
            audit = parse_findings(text, audit_id)
            save_evidence(store, evidence, f"audits/{stage}.json", audit)
            findings = assign_ids(audit)
            review_inputs = baseline_inputs(case, repo, spec)
            review_inputs.update(audit_id=audit_id, audit_kind=kind, findings=findings)
            review, accepted, deferred, exceptions = parse_review(
                invoke(
                    f"review-{number:02d}",
                    render_simple_prompt(protocol.prompts["review"], review_inputs),
                ),
                audit_id,
                findings,
            )
            save_evidence(store, evidence, f"reviews/{stage}.json", review)
        except BenchError as exc:
            item["status"] = exc.kind
            raise
        blockers = sum(f["severity"] != "P3" for f in accepted)
        item.update(
            status="issues_found" if accepted else "clean",
            blocking_issue_count=blockers,
            reported_count=len(findings),
            accepted_count=len(accepted),
            accepted_by_severity={
                s: sum(f["severity"] == s for f in accepted) for s in ("P0", "P1", "P2", "P3")
            },
            review=f"reviews/{stage}.json",
        )
        result["deferred_p3"].extend(
            {"audit_id": audit_id, "artifact_sha256": flow["artifact_sha256"], **f}
            for f in deferred
        )
        if accepted:
            flow["clean"] = []
            flow["pending_fix"] = {"audit_id": audit_id, "kind": kind, "accepted": accepted}
            if spec_only and blockers:
                flow["failure_streak"] += 1
        elif spec_only:
            flow["failure_streak"] = 0
            clean = {"audit_id": audit_id, "sha256": flow["artifact_sha256"]}
            if flow["clean"] and flow["clean"][-1]["sha256"] != clean["sha256"]:
                flow["clean"] = []
            flow["clean"] = (flow["clean"] + [clean])[-2:]
        else:
            flow["phase"] = "spec-freeze-loop"
        item["clean_streak"] = len(flow["clean"])
        store.checkpoint(result, stage + ":reviewed")
        if exceptions:
            result.update(status="BLOCKED", terminal_reason=", ".join(sorted(set(exceptions))))
            return
        if len(flow["clean"]) == 2:
            result.update(
                status="CONVERGED",
                convergence_round=number,
                frozen_spec_sha256=flow["artifact_sha256"],
                clean_audit_ids=[row["audit_id"] for row in flow["clean"]],
                terminal_reason="Two reviewed clean freeze audits on identical Spec bytes",
            )
            flow["phase"] = "FROZEN"
            return
        # Hard benchmark budget takes precedence over a resumable soft pause.
        if result["audit_rounds"] >= maximum:
            result.update(status="NON_CONVERGED", terminal_reason="Total audit budget exhausted")
            return
        if flow["failure_streak"] >= 6:
            result.update(
                status="PAUSED", terminal_reason="Six accepted P0-P2 freeze finding rounds"
            )
            save_evidence(
                store,
                evidence,
                f"pauses/pause-{flow['resume_count'] + 1:02d}.json",
                {
                    "at": utc_now(),
                    "audit_id": audit_id,
                    "failure_streak": flow["failure_streak"],
                    "pending_fix": flow["pending_fix"],
                    "artifact_sha256": flow["artifact_sha256"],
                },
            )
            return


def resume_run(path: Path, adapter):
    """Resume only an intact PAUSED Simple run; all experiment settings remain pinned."""
    path = path.resolve()
    if not path.is_dir():
        raise BenchError("RESUME_ERROR", "Run directory does not exist")
    with run_lock(path):
        try:
            store = object.__new__(RunStore)
            store.path = path
            store.metadata = load_yaml((path / "run.yaml").read_bytes(), "run.yaml")
            store.run_id = store.metadata["run_id"]
            raw_result = (path / "result.json").read_bytes()
            if digest(raw_result) != store.metadata["result_sha256"]:
                raise BenchError("RESUME_ERROR", "Saved result checkpoint changed")
            result = json.loads(raw_result)
            if result["workflow"] != "spec-init-freeze" or result["flow"]["schema_version"] != 1:
                raise BenchError("RESUME_ERROR", "Unsupported resumable workflow")
            if result["status"] != "PAUSED":
                raise BenchError(
                    "RESUME_ERROR", "Only PAUSED runs can resume; BLOCKED needs a new run"
                )
            evidence = Evidence(path, store.metadata["evidence_sha256"])
            evidence.empty_workspaces = list((path / "audit-workspaces").glob("*"))
            evidence.check()
            snapshots = {
                name.removeprefix("input/"): evidence.path(name).read_bytes()
                for name in evidence.known
                if name.startswith("input/")
            }
            case = case_from_snapshots(result["case_id"], snapshots)
            protocol = protocol_from_snapshots(result["protocol_id"], snapshots)
            pinned = json.loads(snapshots["execution-config.json"])
            settings = pinned["effective_config"]
            if any(store.metadata[key] != pinned[key] for key in pinned):
                raise BenchError("RESUME_ERROR", "Execution configuration changed")
            maximum = positive_int(settings["max_audit_rounds"], "max_audit_rounds")
            timeout = positive_int(settings["agent_timeout_seconds"], "agent_timeout_seconds")
            requested = pinned["requested_config"]
            config = RunConfig(
                project_root=Path(requested["project_root"]),
                case_id=case.id,
                runs_dir=path.parent,
                workspace_dir=Path(requested["workspace_dir"]),
                model=settings["model"],
                max_rounds=maximum,
                timeout_seconds=timeout,
                protocol_id=protocol.id,
                reasoning_effort=settings.get("reasoning_effort"),
                provider=settings.get("provider"),
            )
            check_adapter(adapter, config.provider, config.model, config.reasoning_effort)
            if (
                result["agent"]["type"] != adapter.agent_type
                or result["agent"]["version"] != adapter.version()
            ):
                raise BenchError(
                    "RESUME_ERROR", "Resume requires the original adapter type/version"
                )
            if result["audit_rounds"] >= maximum:
                raise BenchError("RESUME_ERROR", "No remaining audit budget")
        except (OSError, KeyError, ValueError, TypeError) as exc:
            raise BenchError("RESUME_ERROR", f"Cannot validate paused run: {exc}") from exc
        # Keep PAUSED intact when the shared checkout is busy, dirty, or has drifted.
        # Hold this same checkout lock through the continuation to avoid a preflight race.
        started = time.monotonic()
        with prepare_repository(case, config.workspace_dir, store.path) as repo:
            expected = json.loads(snapshots["repository-manifest.json"])
            if repo.baseline != expected:
                raise BenchError("RESUME_ERROR", "Repository differs from original run")
            evidence.check()
            flow = result["flow"]
            flow["resume_count"] += 1
            # Archive the previous terminal result before any checkpoint is changed.
            save_evidence(
                store,
                evidence,
                f"resumptions/resume-{flow['resume_count']:02d}.json",
                {
                    "at": utc_now(),
                    "previous_result": json.loads(raw_result),
                    "previous_result_sha256": digest(raw_result),
                },
            )
            flow.update(failure_streak=0, clean=[])
            result.update(status="RUNNING", terminal_reason=None)
            store.metadata.update(ended_at=None, status="RUNNING")
            store.save_metadata()
            store.checkpoint(result, "resumed")
            return execute_simple(
                config,
                adapter,
                store,
                case,
                protocol,
                result,
                maximum,
                timeout,
                evidence,
                started,
                prepared_repo=repo,
            )
