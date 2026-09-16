"""Implement a pinned Spec, audit the full diff, and continue in six-audit batches."""

import json
import time
from pathlib import Path

from .cases import case_from_snapshots, load_yaml, positive_int
from .evidence import Evidence, digest
from .exec_audit import parse_code_result, parse_exec_audit
from .exec_repository import prepare_exec_repository
from .execution import Invoker
from .models import BenchError, RunConfig
from .protocol import protocol_from_snapshots, render_exec_prompt
from .repository_flow import SessionStore, read_spec, session_lock, unfinished_invocation
from .runs import RunStore, atomic_text, utc_now, write_json
from .simple_audit import assign_ids

WORKFLOW = "exec-mrac"
BATCH_SIZE = 6
RESUMABLE = {
    "PAUSED",
    "NEEDS_INPUT",
    "AGENT_ERROR",
    "TIMEOUT",
    "AUDIT_INVALID",
    "FIX_INVALID",
    "INTERNAL_ERROR",
}


class ExecStore(SessionStore):
    def checkpoint(self, result, stage):
        write_json(
            self.path / "exec-state.json",
            {
                "schema_version": 1,
                "result": result,
                "evidence_sha256": self.evidence.known,
                "stage": stage,
                "updated_at": utc_now(),
            },
        )
        self.base.checkpoint(result, stage)


def record_bytes(store, name, content):
    path = store.evidence.path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(content)
    store.evidence.record(name)
    return name


def record_json(store, name, data):
    return record_bytes(
        store, name, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


def save_candidate(store, result, snapshot):
    current = result["flow"]["candidate"]
    if current and current["signature"] == snapshot["signature"]:
        return current
    result["flow"]["candidate_sequence"] += 1
    number = result["flow"]["candidate_sequence"]
    folder = f"candidates/{number:04d}"
    patch = record_bytes(store, folder + "/changes.patch", snapshot["patch"])
    manifest = record_json(
        store,
        folder + "/manifest.json",
        {key: value for key, value in snapshot.items() if key != "patch"},
    )
    candidate = {
        "signature": snapshot["signature"],
        "tree": snapshot["tree"],
        "manifest": manifest,
        "patch": patch,
        "patch_sha256": snapshot["patch_sha256"],
    }
    result["flow"]["candidate"] = candidate
    result["final_artifact"] = patch
    return candidate


def run_exec(config, adapter, base, case, protocol, result, timeout, started):
    if config.spec_file.suffix.lower() != ".md":
        raise BenchError("CASE_ERROR", "Execution Spec must be an explicit Markdown file")
    try:
        spec = read_spec(config.spec_file.resolve())
    except BenchError as exc:
        raise BenchError("CASE_ERROR", str(exc)) from exc
    with session_lock(base.path):
        base.snapshot("execution-spec.md", spec)
        base.metadata["execution_spec"] = {
            "source": str(config.spec_file.resolve()),
            "sha256": digest(spec),
        }
        base.save_metadata()
        base.snapshot(
            "execution-config.json",
            json.dumps(
                {
                    key: base.metadata[key]
                    for key in (
                        "requested_config",
                        "effective_config",
                        "agent",
                        "protocol_id",
                        "protocol_version",
                        "execution_spec",
                    )
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
        )
        evidence = Evidence(base.path)
        for name in base.metadata["input_sha256"]:
            evidence.record("input/" + name)
        store = ExecStore(base, evidence)
        result.update(
            workflow=WORKFLOW,
            artifact_type="code",
            implement_rounds=0,
            spec_sha256=digest(spec),
            final_checkout=None,
            final_candidate_sha256=None,
            clean_audit_ids=[],
            terminal_reason=None,
            flow={
                "phase": "IMPLEMENT",
                "audit_limit": BATCH_SIZE,
                "batch_size": BATCH_SIZE,
                "budget_extensions": [],
                "pending_fix": None,
                "clean": [],
                "candidate": None,
                "candidate_sequence": 0,
                "sequences": {"implement": 0, "audit": 0, "repair": 0},
                "active_audit": None,
                "auditor_ids": [],
                "last_call": None,
                "validation": [],
                "questions": [],
                "responses": [],
                "resume_count": 0,
            },
        )
        store.checkpoint(result, "initialized")
        return execute_exec(
            config, adapter, store, case, protocol, result, timeout, started, create=True
        )


class ExecEngine:
    def __init__(self, store, result, case, protocol, repo, config, adapter, timeout):
        self.store, self.result, self.flow = store, result, result["flow"]
        self.case, self.protocol, self.repo = case, protocol, repo
        self.spec = store.evidence.path("input/execution-spec.md").read_bytes().decode("utf-8-sig")
        self.invoke = Invoker(
            store,
            result,
            repo,
            adapter,
            config.model,
            timeout,
            config.reasoning_effort,
            store.evidence.check,
            store.evidence.capture,
        )

    def inputs(self):
        snapshot, candidate = self.repo.last_snapshot, self.flow["candidate"]
        return {
            "execution_spec": self.spec,
            "spec_sha256": self.result["spec_sha256"],
            "repository_path": str(self.repo.path),
            "base_head": self.case.commit,
            "candidate_sha256": candidate["signature"],
            "candidate_tree": candidate["tree"],
            "diff_path": str(self.store.evidence.path(candidate["patch"])),
            "diff_sha256": candidate["patch_sha256"],
            "product_changes": snapshot["changes"],
            "ignored_files": list(snapshot["ignored_files"]),
            "validation": self.flow["validation"],
        }

    def call(self, role, inputs, item=None):
        self.flow["sequences"][role] += 1
        stage = f"{role}-{self.flow['sequences'][role]:02d}"
        readonly = role == "audit"
        self.repo.expected_signature = self.flow["candidate"]["signature"]
        self.repo.expected_workspace = None
        self.flow["last_call"] = {
            "stage": stage,
            "role": role,
            "counter_before": self.result[f"{role}_rounds"],
        }
        self.store.checkpoint(self.result, stage + ":prepared")
        text = self.invoke(
            stage,
            render_exec_prompt(self.protocol.prompts[role], inputs, readonly=readonly),
            item,
            readonly=readonly,
        )
        self.flow["last_call"] = None
        if not readonly:
            save_candidate(self.store, self.result, self.repo.last_snapshot)
        self.store.checkpoint(self.result, stage + ":returned")
        return stage, text

    def code(self):
        repair = self.flow["phase"] == "FIX"
        pending = self.flow["pending_fix"] if repair else None
        inputs = self.inputs()
        inputs["user_responses"] = [
            self.store.evidence.path(name).read_text(encoding="utf-8-sig")
            for name in self.flow["responses"]
        ]
        if repair:
            inputs["current_findings"] = pending["findings"]
        before = self.flow["candidate"]
        self.flow["clean"] = []
        role = "repair" if repair else "implement"
        stage, text = self.call(role, inputs)
        outcome = parse_code_result(text, pending["findings"] if pending else None)
        folder = "repairs" if repair else "implementations"
        record_json(self.store, f"{folder}/{stage}.json", outcome)
        self.flow["validation"] = outcome["validation"]
        if outcome["disposition"] == "needs_input":
            self.flow["questions"] = outcome["questions"]
            self.result.update(status="NEEDS_INPUT", terminal_reason=outcome["summary"])
            return
        if repair and self.flow["candidate"]["tree"] == before["tree"]:
            raise BenchError("FIX_INVALID", "Repair did not change the product diff")
        if pending:
            for item in self.result["trajectory"]:
                if item["audit_id"] == pending["audit_id"]:
                    item["repair_candidate"] = dict(self.flow["candidate"])
        self.flow.update(phase="AUDIT", pending_fix=None, questions=[])
        self.store.checkpoint(self.result, stage + ":saved")

    def audit(self):
        number = self.flow["sequences"]["audit"] + 1
        audit_id = f"{self.store.run_id}-audit-{number:02d}"
        candidate = self.flow["candidate"]
        item = {
            "audit_id": audit_id,
            "audit_round": self.result["audit_rounds"] + 1,
            "candidate_sha256": candidate["signature"],
            "spec_sha256": self.result["spec_sha256"],
            "artifact": candidate["manifest"],
            "status": "RUNNING",
            "issue_count": None,
            "repair_candidate": None,
        }
        self.flow["active_audit"] = item
        inputs = self.inputs()
        inputs["audit_id"] = audit_id
        try:
            stage, text = self.call("audit", inputs, item)
            metadata = json.loads(
                (self.store.path / "raw" / stage / "execution.json").read_bytes()
            )["metadata"]
            auditor_id = metadata.get("thread_id")
            if self.invoke.adapter.agent_type == "codex_exec" and not auditor_id:
                raise BenchError("AUDIT_INVALID", "Missing fresh Codex auditor session ID")
            auditor_id = auditor_id or f"invocation:{audit_id}"
            if not isinstance(auditor_id, str) or auditor_id in self.flow["auditor_ids"]:
                raise BenchError("AUDIT_INVALID", "Auditor identity reused or invalid")
            self.flow["auditor_ids"].append(auditor_id)
            item["auditor_id"] = auditor_id
            audit = parse_exec_audit(
                text, audit_id, self.result["spec_sha256"], candidate["signature"]
            )
            item["audit"] = record_json(self.store, f"audits/{stage}.json", audit)
        except BenchError as exc:
            item["status"] = exc.kind
            raise
        findings = assign_ids(audit)
        self.flow["active_audit"] = None
        item.update(issue_count=len(findings), status="issues_found" if findings else "clean")
        if findings:
            self.flow.update(
                phase="FIX", clean=[], pending_fix={"audit_id": audit_id, "findings": findings}
            )
        else:
            clean = {
                "audit_id": audit_id,
                "auditor_id": auditor_id,
                "candidate_sha256": candidate["signature"],
                "spec_sha256": self.result["spec_sha256"],
                "base_head": self.case.commit,
            }
            if self.flow["clean"] and any(
                self.flow["clean"][-1][key] != clean[key]
                for key in ("candidate_sha256", "spec_sha256", "base_head")
            ):
                self.flow["clean"] = []
            self.flow["clean"] = (self.flow["clean"] + [clean])[-2:]
            if len(self.flow["clean"]) == 2:
                self.flow["phase"] = "CONVERGED"
                self.result.update(
                    status="CONVERGED",
                    convergence_round=self.result["audit_rounds"],
                    final_candidate_sha256=candidate["signature"],
                    clean_audit_ids=[row["audit_id"] for row in self.flow["clean"]],
                    terminal_reason="Two zero-finding audits on the same Spec and candidate",
                )
        item["clean_streak"] = len(self.flow["clean"])
        self.store.checkpoint(self.result, stage + ":parsed")

    def drive(self):
        while self.result["status"] == "RUNNING":
            self.store.evidence.check()
            if self.result["audit_rounds"] >= self.flow["audit_limit"]:
                self.repo.expected_signature = self.flow["candidate"]["signature"]
                self.repo.expected_workspace = None
                if self.repo.inspect()["violation"]:
                    raise BenchError("PROTOCOL_VIOLATION", "Checkout changed before budget pause")
                # Preserve both pending findings and a possible first clean. Do not repair here.
                self.result.update(
                    status="PAUSED", terminal_reason="Six-audit batch exhausted; resume adds six"
                )
                record_json(
                    self.store,
                    f"pauses/pause-{self.flow['resume_count'] + 1:02d}.json",
                    {
                        "at": utc_now(),
                        "audit_limit": self.flow["audit_limit"],
                        "candidate": self.flow["candidate"],
                        "pending_fix": self.flow["pending_fix"],
                        "clean": self.flow["clean"],
                    },
                )
                return
            if self.flow["phase"] in {"IMPLEMENT", "FIX"}:
                self.code()
            else:
                self.audit()


def execute_exec(
    config,
    adapter,
    store,
    case,
    protocol,
    result,
    timeout,
    started,
    *,
    create=False,
    prepared_repo=None,
):
    engine, repo = None, prepared_repo
    try:
        repo = repo or prepare_exec_repository(
            case, config.workspace_dir, store.path, create=create
        )
        repo.temporary_root = store.path / ".scratch"
        repo.temporary_root.mkdir(exist_ok=True)
        control_file = "input/git-control.json"
        if control_file in store.evidence.known:
            if json.loads(store.evidence.path(control_file).read_bytes()) != repo.control:
                raise BenchError("PROTOCOL_VIOLATION", "Git baseline/config/index/refs changed")
        else:
            record_json(store, control_file, repo.control)
        result["final_checkout"] = str(repo.path)
        store.metadata["repository"]["workspace"] = str(repo.path)
        store.save_metadata()
        if repo.inspect()["violation"]:
            raise BenchError("PROTOCOL_VIOLATION", "Invalid writable repository boundary")
        if result["flow"]["candidate"] is None:
            save_candidate(store, result, repo.last_snapshot)
        elif repo.last_snapshot["signature"] != result["flow"]["candidate"]["signature"]:
            raise BenchError("CANDIDATE_CHANGED", "Candidate changed since the saved checkpoint")
        engine = ExecEngine(store, result, case, protocol, repo, config, adapter, timeout)
        engine.drive()
        store.evidence.check()
    except BenchError as exc:
        result.update(
            status=exc.kind,
            protocol_violation=exc.kind == "PROTOCOL_VIOLATION",
            error={
                "type": exc.kind,
                "message": str(exc),
                "stage": engine.invoke.stage if engine else "repository",
            },
        )
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001
        result.update(
            status="INTERNAL_ERROR",
            error={
                "type": "INTERNAL_ERROR",
                "message": str(exc) or type(exc).__name__,
                "stage": engine.invoke.stage if engine else "repository",
            },
        )
    if result["error"]:
        result["flow"]["clean"] = []
        # A failed writer may leave useful partial code. Capture it without pretending it
        # completed implementation/repair; an auditor mutation is never promoted.
        if (
            engine
            and result["flow"]["phase"] in {"IMPLEMENT", "FIX"}
            and not result["protocol_violation"]
        ):
            repo.expected_signature = None
            repo.expected_workspace = None
            if not repo.inspect()["violation"]:
                save_candidate(store, result, repo.last_snapshot)
    result["usage"]["wall_time_seconds"] = round(
        (result["usage"]["wall_time_seconds"] or 0) + time.monotonic() - started, 3
    )
    store.finish(result)
    write_report(store, result)
    return store.path, result


def write_report(store, result):
    flow = result["flow"]
    lines = [
        f"# Exec-MRAC: {store.run_id}",
        "",
        f"Status: {result['status']}",
        f"Phase: {flow['phase']}",
        f"Spec SHA-256: {result['spec_sha256']}",
        f"Checkout: {result['final_checkout']}",
        f"Candidate patch: {result['final_artifact']}",
        f"Audits: {result['audit_rounds']} / {flow['audit_limit']}",
        f"Implement calls: {result['implement_rounds']}; repair calls: {result['repair_rounds']}",
    ]
    if result["terminal_reason"]:
        lines.append(result["terminal_reason"])
    if result["clean_audit_ids"]:
        lines.append("Clean audits: " + ", ".join(result["clean_audit_ids"]))
    for row in result["trajectory"]:
        lines.append(
            f"R{row['audit_round']}: {row['status']}, findings={row['issue_count']}, candidate={row['candidate_sha256']}"
        )
    for question in flow["questions"]:
        lines.append("Required input: " + question)
    for check in flow["validation"]:
        lines.append(f"Verification ({check['status']}): {check['command']} — {check['evidence']}")
    lines += [
        "",
        "Validation entries are agent-reported; raw command events are preserved separately.",
        "Convergence means two zero-finding reviews, not proof that the implementation is defect-free.",
    ]
    atomic_text(store.path / "run-report.md", "\n".join(lines) + "\n")


def load_exec(path):
    try:
        saved = json.loads((path / "exec-state.json").read_bytes())
        result = saved["result"]
        if saved["schema_version"] != 1 or result["workflow"] != WORKFLOW:
            raise BenchError("RESUME_ERROR", "Unsupported exec protocol state")
        evidence = Evidence(path, saved["evidence_sha256"])
        evidence.check()
        base = object.__new__(RunStore)
        base.path, base.run_id = path, result["run_id"]
        base.metadata = load_yaml((path / "run.yaml").read_bytes(), "run.yaml")
        snapshots = {
            name[6:]: evidence.path(name).read_bytes()
            for name in evidence.known
            if name.startswith("input/")
        }
        pinned = json.loads(snapshots["execution-config.json"])
        if any(base.metadata[key] != value for key, value in pinned.items()):
            raise BenchError("RESUME_ERROR", "Execution configuration changed")
        if digest(snapshots["execution-spec.md"]) != result["spec_sha256"]:
            raise BenchError("RESUME_ERROR", "Execution Spec identity changed")
        case = case_from_snapshots(result["case_id"], snapshots)
        protocol = protocol_from_snapshots(result["protocol_id"], snapshots)
        if protocol.workflow != WORKFLOW:
            raise BenchError("RESUME_ERROR", "Cannot migrate another protocol into exec-mrac")
        flow = result["flow"]
        if flow["batch_size"] != 6 or flow["audit_limit"] != 6 * (
            1 + len(flow["budget_extensions"])
        ):
            raise BenchError("RESUME_ERROR", "Audit budget does not match six-round extensions")
        requested, settings = pinned["requested_config"], pinned["effective_config"]
        config = RunConfig(
            Path(requested["project_root"]),
            case.id,
            path.parent,
            Path(requested["workspace_dir"]),
            settings["model"],
            6,
            positive_int(settings["agent_timeout_seconds"], "timeout"),
            protocol.id,
            settings.get("reasoning_effort"),
            Path(pinned["execution_spec"]["source"]),
        )
        return ExecStore(base, evidence), result, case, protocol, config
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise BenchError("RESUME_ERROR", f"Cannot validate exec run: {exc}") from exc


def verify_checkout(store, result, case, config):
    repo = prepare_exec_repository(case, config.workspace_dir, store.path, create=False)
    repo.temporary_root = store.path / ".scratch"
    repo.temporary_root.mkdir(exist_ok=True)
    original = json.loads(store.evidence.path("input/git-control.json").read_bytes())
    if repo.control != original:
        raise BenchError("RESUME_ERROR", "Git baseline/config/index/refs changed")
    repo.expected_signature = result["flow"]["candidate"]["signature"]
    if repo.inspect()["violation"]:
        raise BenchError(
            "CANDIDATE_CHANGED", "Saved code candidate changed; old clean audits cannot be reused"
        )
    return repo


def reconcile_failed_call(store, result, invocation):
    flow = result["flow"]
    call = flow["last_call"]
    if call:
        field = f"{call['role']}_rounds"
        if invocation and invocation.get("started") and result[field] == call["counter_before"]:
            result[field] += 1
            if call["role"] == "audit" and flow["active_audit"]:
                result["trajectory"].append(dict(flow["active_audit"]))
        for path in sorted((store.path / "raw" / call["stage"]).rglob("*")):
            name = path.relative_to(store.path).as_posix()
            if path.is_file() and name not in store.evidence.known:
                store.evidence.record(name)
        flow["last_call"] = None
    if flow["active_audit"]:
        for row in result["trajectory"]:
            if row["audit_id"] == flow["active_audit"]["audit_id"]:
                row["status"] = "abandoned"
        flow.update(active_audit=None, clean=[])


def resume_exec(path, adapter, input_file=None):
    path = path.resolve()
    if not path.is_dir():
        raise BenchError("RESUME_ERROR", "Run directory does not exist")
    with session_lock(path):
        store, result, case, protocol, config = load_exec(path)
        if result["status"] not in RESUMABLE:
            raise BenchError("RESUME_ERROR", f"Cannot resume {result['status']}")
        invocation = unfinished_invocation(store, result)
        if (
            result["agent"]["type"] != adapter.agent_type
            or result["agent"]["version"] != adapter.version()
        ):
            raise BenchError("RESUME_ERROR", "Resume requires the original adapter type/version")
        if input_file and result["flow"]["phase"] not in {"IMPLEMENT", "FIX"}:
            raise BenchError("RESUME_ERROR", "Input is only accepted by a pending writable stage")
        response = read_spec(input_file.resolve()) if input_file else None
        if result["status"] == "NEEDS_INPUT" and response is None:
            return path, result
        # Validate the entire product candidate before changing the budget or checkpoint.
        repo = verify_checkout(store, result, case, config)
        started = time.monotonic()
        flow = result["flow"]
        number = flow["resume_count"] + 1
        record_json(
            store,
            f"resumptions/resume-{number:02d}.json",
            {
                "at": utc_now(),
                "previous_result": result,
            },
        )
        reconcile_failed_call(store, result, invocation)
        if result["audit_rounds"] >= flow["audit_limit"]:
            extension = {
                "at": utc_now(),
                "from": flow["audit_limit"],
                "to": flow["audit_limit"] + 6,
                "resume": number,
            }
            flow["budget_extensions"].append(extension)
            flow["audit_limit"] += 6
        if response is not None:
            name = record_bytes(store, f"responses/response-{number:02d}.md", response)
            flow["responses"].append(name)
        flow["resume_count"] = number
        result.update(status="RUNNING", error=None, terminal_reason=None)
        store.metadata.update(status="RUNNING", ended_at=None)
        store.save_metadata()
        store.checkpoint(result, "resumed")
        return execute_exec(
            config,
            adapter,
            store,
            case,
            protocol,
            result,
            config.timeout_seconds,
            started,
            prepared_repo=repo,
        )


def inspect_exec(path, *, abort_reason=None):
    from contextlib import nullcontext

    path = path.resolve()
    if not path.is_dir():
        raise BenchError("RESUME_ERROR", "Run directory does not exist")
    with session_lock(path) if abort_reason is not None else nullcontext():
        store, result, case, _, config = load_exec(path)
        if result["status"] != "RUNNING" and result["flow"]["candidate"] is not None:
            verify_checkout(store, result, case, config)
        if abort_reason is not None:
            if not abort_reason.strip() or result["status"] in {"CONVERGED", "ABORTED"}:
                raise BenchError("RESUME_ERROR", "Abort requires a reason and a nonterminal run")
            invocation = unfinished_invocation(store, result)
            record_json(
                store,
                f"resumptions/abort-{result['flow']['resume_count'] + 1:02d}.json",
                {
                    "at": utc_now(),
                    "reason": abort_reason,
                    "previous_result": result,
                },
            )
            reconcile_failed_call(store, result, invocation)
            result["flow"]["phase"] = "ABORTED"
            result.update(status="ABORTED", terminal_reason=abort_reason)
            store.finish(result)
        if result["status"] != "RUNNING":
            write_report(store, result)
        return path, result
