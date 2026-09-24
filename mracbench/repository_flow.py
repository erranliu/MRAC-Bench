"""Repository-backed MRAC-Spec: every audit finding enters FIX without adjudication."""

import json
import os
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path

from .cases import case_from_snapshots, load_yaml, positive_int
from .evidence import Evidence, digest
from .execution import Invoker
from .models import BenchError, RunConfig
from .protocol import (
    protocol_from_snapshots,
    render_repository_mcp_prompt,
    render_repository_prompt,
    render_spec_checkout_prompt,
)
from .providers import check_adapter
from .repository import git, prepare_repository
from .repository_audit import parse_audit, parse_repair
from .repository_mcp import server_config
from .runs import RunStore, atomic_text, utc_now, write_json
from .simple_audit import assign_ids, decode
from .simple_repair import unified_spec_diff
from .spec_checkout import SpecCheckout, spec_path

WORKFLOW = "repository-spec-freeze"
STATE_VERSION = 3
PROTOCOL_VERSION = 4
RESUMABLE = {
    "PROVIDER_ERROR",
    "RUNNING",
    "PAUSED",
    "NEEDS_INPUT",
    "AGENT_ERROR",
    "TIMEOUT",
    "PARSE_ERROR",
    "AUDIT_INVALID",
    "FIX_INVALID",
    "AUDIT_STALE",
    "INTERNAL_ERROR",
    "REPOSITORY_ERROR",
    "REPOSITORY_ACCESS_ERROR",
}


@contextmanager
def session_lock(path):
    """OS lock releases on crash; checkpoint files do not act as live-process locks."""
    with (path / ".repository-run.lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise BenchError("RESUME_ERROR", "Run is still active") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # Access denied is not proof of termination.
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class SessionStore:
    """Checkpoint hook for Invoker; the v2 checkpoint is the atomic canonical state."""

    def __init__(self, base, evidence):
        self.base = base
        self.evidence = evidence

    def __getattr__(self, name):
        return getattr(self.base, name)

    def checkpoint(self, result, stage):
        write_json(
            self.path / "repository-state.json",
            {
                "schema_version": STATE_VERSION,
                "result": result,
                "evidence_sha256": self.evidence.known,
                "stage": stage,
                "updated_at": utc_now(),
            },
        )
        self.base.checkpoint(result, stage)

    def finish(self, result):
        self.base.metadata["evidence_sha256"] = self.evidence.known
        self.base.finish(result)
        self.checkpoint(result, "finished")


def save_record(store, folder, name, data):
    relative = f"{folder}/{name}.json"
    path = store.evidence.path(relative)
    path.parent.mkdir(exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    store.evidence.record(relative)
    return relative


def read_spec(path):
    try:
        raw = path.read_bytes()
        if not raw.decode("utf-8-sig").strip():
            raise BenchError("AUDIT_STALE", "Spec or supplied input is empty")
        return raw
    except (OSError, UnicodeError) as exc:
        raise BenchError("RESUME_ERROR", f"Cannot read UTF-8 Spec/input: {path}: {exc}") from exc


def parse_needs_input(text, audit_id):
    try:
        value = decode(text)
        if (
            not isinstance(value, dict)
            or set(value) != {"disposition", "reason", "questions"}
            or value["disposition"] != "needs_input"
            or not isinstance(value["reason"], str)
            or not value["reason"].strip()
            or not isinstance(value["questions"], list)
            or not value["questions"]
            or any(not isinstance(q, str) or not q.strip() for q in value["questions"])
        ):
            raise ValueError("Unchanged Spec requires a concrete needs_input response")
        return {"audit_id": audit_id, **value}
    except (ValueError, TypeError) as exc:
        raise BenchError("FIX_INVALID", str(exc)) from exc


def verify_audit_repository_reads(raw, head):
    try:
        events = [
            json.loads(line)
            for line in (raw / "repository-read-events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line
        ]
        if not any(
            event.get("tool") == "repository_head"
            and event.get("ok") is True
            and event.get("result", {}).get("head") == head
            for event in events
        ):
            raise ValueError("Audit did not verify the fixed repository HEAD")
        if not any(
            event.get("tool") == "repository_read"
            and event.get("ok") is True
            and event.get("result", {}).get("lines")
            for event in events
        ):
            raise ValueError("Audit did not read repository content")
    except (OSError, ValueError, TypeError) as exc:
        raise BenchError("REPOSITORY_ACCESS_ERROR", str(exc)) from exc


def save_artifact(store, name, raw):
    relative = f"artifacts/{name}.md"
    with store.evidence.path(relative).open("xb") as stream:
        stream.write(raw)
    store.evidence.record(relative)
    return relative


def run_repository_flow(config, adapter, base, case, protocol, result, maximum, timeout, started):
    if protocol.version not in {2, 3, PROTOCOL_VERSION}:
        raise BenchError("CASE_ERROR", "Unsupported protocol revision; use spec-mrac-v2@4")
    with session_lock(base.path):
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
                    )
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
        )
        evidence = Evidence(base.path)
        for name in base.metadata["input_sha256"]:
            evidence.record("input/" + name)
        store = SessionStore(base, evidence)
        raw = case.snapshots["task.md"]
        current = save_artifact(store, "spec.initial", raw)
        working = evidence.path("working/spec.md")
        working.parent.mkdir()
        working.write_bytes(raw)
        result.update(
            workflow=WORKFLOW,
            frozen_spec_sha256=None,
            clean_audit_ids=[],
            terminal_reason=None,
            final_artifact=current,
            flow={
                "schema_version": STATE_VERSION,
                "phase": "AUDIT",
                "base_head": case.commit,
                "clean": [],
                "failure_streak": 0,
                "pending_fix": None,
                "active_audit": None,
                "sequence": 0,
                "auditor_ids": [],
                "repair_sequence": 0,
                "resume_count": 0,
                "last_call": None,
                "artifact_sha256": digest(raw),
                "questions": [],
                "responses": [],
            },
        )
        store.checkpoint(result, "copied")
        return execute(config, adapter, store, case, protocol, result, maximum, timeout, started)


class Engine:
    def __init__(self, store, case, protocol, result, repo, adapter, config, timeout):
        self.store, self.case, self.protocol = store, case, protocol
        self.result, self.repo, self.flow = result, repo, result["flow"]
        self.invoke = Invoker(
            store,
            result,
            repo,
            adapter,
            config.model,
            timeout,
            config.reasoning_effort,
            self.guard,
            store.evidence.capture,
        )

    def guard(self):
        self.store.evidence.check()
        if (
            digest(read_spec(self.store.evidence.path("working/spec.md")))
            != self.flow["artifact_sha256"]
        ):
            raise BenchError(
                "AUDIT_STALE", "Working Spec changed; resume to audit the new revision"
            )

    def inputs(self):
        return {
            "source_spec": self.case.task,
            "source_spec_sha256": digest(self.case.snapshots["task.md"]),
            "current_spec": read_spec(self.store.evidence.path("working/spec.md")).decode(
                "utf-8-sig"
            ),
            "spec_sha256": self.flow["artifact_sha256"],
            "repository_path": str(self.repo.path),
            "fixed_repository_head": self.repo.commit,
            "related_specs": self.case.related_specs,
            "repository_instruction_paths": json.loads(
                (self.store.path / "input/repository-instructions.json").read_text(encoding="utf-8")
            ),
        }

    def call(self, stage, role, inputs, item=None, *, prompt=None, **invoke_options):
        self.flow["last_call"] = {
            "stage": stage,
            "role": role,
            "counter_before": self.result[f"{role}_rounds"],
        }
        self.store.checkpoint(self.result, stage + ":prepared")
        text = self.invoke(
            stage,
            prompt
            if prompt is not None
            else render_repository_prompt(self.protocol.prompts[role], inputs),
            item,
            **invoke_options,
        )
        self.flow["last_call"] = None
        self.store.checkpoint(self.result, stage + ":returned")
        return text

    def repair_inputs(self):
        inputs = self.inputs()
        inputs["user_responses"] = [
            {
                "file": name,
                "content": self.store.evidence.path(name).read_text(encoding="utf-8-sig"),
            }
            for name in self.flow["responses"]
        ]
        return inputs

    def repair(self, maximum):
        pending = self.flow["pending_fix"]
        inputs = self.repair_inputs()
        inputs.update(audit_id=pending["audit_id"], findings=pending["findings"])
        self.flow["repair_sequence"] += 1
        stage = f"repair-{self.flow['repair_sequence']:02d}"
        if self.protocol.version >= 3:
            checkout_path = self.store.path / "checkout"
            checkout = {}
            current = self.store.evidence.path("working/spec.md").read_bytes()
            name = spec_path(self.case)

            def setup_checkout(_raw):
                checkout["value"] = SpecCheckout(self.repo, checkout_path, name, current)

            checkout_inputs = {
                **inputs,
                "spec_path": name,
                "repository_path": str(checkout_path),
            }
            checkout_inputs.pop("current_spec")
            reply = self.call(
                stage,
                "repair",
                checkout_inputs,
                prompt=render_spec_checkout_prompt(
                    self.protocol.prompts["repair"], checkout_inputs
                ),
                workspace=checkout_path,
                readonly=False,
                workspace_setup=setup_checkout,
            )
            candidate = checkout["value"].candidate()
            if candidate == current:
                repair = parse_needs_input(reply, pending["audit_id"])
            else:
                try:
                    response = decode(reply)
                except (ValueError, TypeError):
                    response = None
                if isinstance(response, dict) and response.get("disposition") == "needs_input":
                    raise BenchError(
                        "FIX_INVALID", "needs_input cannot include a partial Spec edit"
                    )
                if len(candidate) > 8 * 1024 * 1024:
                    raise BenchError("FIX_INVALID", "Repaired Spec exceeds 8 MiB")
                try:
                    if not candidate.decode("utf-8-sig").strip():
                        raise ValueError("Repaired Spec is empty")
                except (UnicodeError, ValueError) as exc:
                    raise BenchError("FIX_INVALID", str(exc)) from exc
                diff_name = f"repairs/{stage}.diff"
                candidate_name = f"raw/{stage}/candidate.md"
                with self.store.evidence.path(candidate_name).open("xb") as stream:
                    stream.write(candidate)
                self.store.evidence.record(candidate_name)
                diff_path = self.store.evidence.path(diff_name)
                diff_path.parent.mkdir(exist_ok=True)
                with diff_path.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(unified_spec_diff(current, candidate))
                self.store.evidence.record(diff_name)
                repair = {
                    "audit_id": pending["audit_id"],
                    "disposition": "continue",
                    "finding_ids": [f["finding_id"] for f in pending["findings"]],
                    "candidate_sha256": digest(candidate),
                    "candidate_path": candidate_name,
                    "diff": diff_name,
                    "summary": reply.strip(),
                }
        else:
            repair = parse_repair(
                self.call(stage, "repair", inputs), pending["audit_id"], pending["findings"]
            )
        save_record(self.store, "repairs", stage, repair)
        if repair["disposition"] == "needs_input":
            self.flow["questions"] = repair["questions"]
            self.result.update(status="NEEDS_INPUT", terminal_reason=repair["reason"])
            return
        raw = candidate if self.protocol.version >= 3 else repair["spec"].encode("utf-8")
        if digest(raw) == self.flow["artifact_sha256"]:
            raise BenchError("FIX_INVALID", "Repair did not change Spec bytes")
        artifact = save_artifact(self.store, f"spec.round-{self.flow['repair_sequence']:02d}", raw)
        self.store.evidence.path("working/spec.md").write_bytes(raw)
        for row in self.result["trajectory"]:
            if row["audit_id"] == pending["audit_id"]:
                row["repair_artifact"] = artifact
        self.result["final_artifact"] = artifact
        self.flow.update(
            phase="AUDIT", artifact_sha256=digest(raw), pending_fix=None, clean=[], questions=[]
        )
        self.store.checkpoint(self.result, stage + ":saved")
        # Pause AFTER the sixth repair, never with an unclosed finding.
        if maximum is not None and self.result["audit_rounds"] >= maximum:
            self.result.update(
                status="NON_CONVERGED", terminal_reason="Explicit audit budget exhausted"
            )
            return
        if self.flow["failure_streak"] >= 6:
            self.flow["phase"] = "PAUSED"
            self.result.update(
                status="PAUSED", terminal_reason="Six rounds with P0-P2 findings; repairs recorded"
            )
            save_record(
                self.store,
                "pauses",
                f"pause-{self.flow['resume_count'] + 1:02d}",
                {
                    "at": utc_now(),
                    "audit_id": pending["audit_id"],
                    "artifact_sha256": digest(raw),
                    "failure_streak": self.flow["failure_streak"],
                },
            )

    def audit(self):
        self.flow["sequence"] += 1
        stage = f"audit-{self.flow['sequence']:02d}"
        audit_id = f"{self.store.run_id}-{stage}"
        item = {
            "audit_round": self.result["audit_rounds"] + 1,
            "audit_id": audit_id,
            "auditor_id": f"fresh-exec:{audit_id}",
            "artifact": self.result["final_artifact"],
            "artifact_sha256": self.flow["artifact_sha256"],
            "base_head": self.repo.commit,
            "status": "RUNNING",
            "blocking_issue_count": None,
            "repair_artifact": None,
        }
        self.flow["active_audit"] = item
        inputs = self.inputs()
        inputs["audit_id"] = audit_id
        try:
            if self.protocol.version >= 4:
                raw = self.store.path / "raw" / stage
                servers = server_config(
                    self.repo.path,
                    self.repo.commit,
                    self.store.path / "input" / "repository-manifest.json",
                    raw / "repository-read-events.jsonl",
                )
                text = self.call(
                    stage,
                    "audit",
                    inputs,
                    item,
                    prompt=render_repository_mcp_prompt(self.protocol.prompts["audit"], inputs),
                    mcp_servers=servers,
                )
                verify_audit_repository_reads(raw, self.repo.commit)
            else:
                text = self.call(stage, "audit", inputs, item)
            execution = json.loads(
                (self.store.path / "raw" / stage / "execution.json").read_bytes()
            )
            actual_id = execution["metadata"].get("thread_id")
            if self.invoke.adapter.agent_type == "codex_exec" and not actual_id:
                raise BenchError("AUDIT_INVALID", "Codex did not report a fresh audit session ID")
            item["auditor_id"] = actual_id or item["auditor_id"]
            if item["auditor_id"] in self.flow["auditor_ids"]:
                raise BenchError("AUDIT_INVALID", "Auditor identity reused")
            self.flow["auditor_ids"].append(item["auditor_id"])
            audit = parse_audit(text, audit_id, self.repo)
            item["audit"] = save_record(self.store, "audits", stage, audit)
            findings = assign_ids(audit)
        except BenchError as exc:
            item["status"] = exc.kind
            raise
        blockers = sum(f["severity"] in ("P0", "P1", "P2") for f in findings)
        item.update(
            status="issues_found" if findings else "clean",
            reported_count=len(findings),
            blocking_issue_count=blockers,
            reported_by_severity={
                s: sum(f["severity"] == s for f in findings) for s in ("P0", "P1", "P2", "P3")
            },
        )
        if "repository_review" in audit:
            item["repository_review"] = audit["repository_review"]
        self.flow["active_audit"] = None
        if findings:
            self.flow.update(
                phase="FIX",
                clean=[],
                questions=[],
                pending_fix={
                    "audit_id": audit_id,
                    "findings": findings,
                    "before_sha256": self.flow["artifact_sha256"],
                },
            )
            self.flow["failure_streak"] = self.flow["failure_streak"] + 1 if blockers else 0
        else:
            self.flow["failure_streak"] = 0
            clean = {
                "audit_id": audit_id,
                "auditor_id": item["auditor_id"],
                "sha256": self.flow["artifact_sha256"],
                "base_head": self.repo.commit,
            }
            previous = self.flow["clean"]
            if previous and any(previous[-1][k] != clean[k] for k in ("sha256", "base_head")):
                previous = []
            if any(row["auditor_id"] == clean["auditor_id"] for row in previous):
                raise BenchError("AUDIT_INVALID", "Auditor identity reused")
            self.flow["clean"] = (previous + [clean])[-2:]
            if len(self.flow["clean"]) == 2:
                self.flow["phase"] = "FROZEN"
                self.result.update(
                    status="CONVERGED",
                    convergence_round=self.result["audit_rounds"],
                    frozen_spec_sha256=self.flow["artifact_sha256"],
                    clean_audit_ids=[r["audit_id"] for r in self.flow["clean"]],
                    terminal_reason="Two empty-findings audits at the fixed baseline",
                )
        item["clean_streak"] = len(self.flow["clean"])
        self.store.checkpoint(self.result, stage + ":recorded")

    def drive(self, maximum):
        while self.result["status"] == "RUNNING":
            self.guard()
            if self.flow["phase"] == "FIX":
                self.repair(maximum)
                # Finish every repair even at the last explicitly budgeted audit.
                continue
            if maximum is not None and self.result["audit_rounds"] >= maximum:
                self.result.update(
                    status="NON_CONVERGED", terminal_reason="Explicit audit budget exhausted"
                )
                return
            self.audit()


def execute(
    config, adapter, store, case, protocol, result, maximum, timeout, started, prepared_repo=None
):
    engine = None
    try:
        with (
            nullcontext(prepared_repo)
            if prepared_repo is not None
            else prepare_repository(case, config.workspace_dir, store.path)
        ) as repo:
            name = "input/repository-manifest.json"
            if name in store.evidence.known:
                if json.loads(store.evidence.path(name).read_bytes()) != repo.baseline:
                    raise BenchError(
                        "PROTOCOL_VIOLATION", "Repository differs from original baseline"
                    )
            else:
                save_record(store, "input", "repository-manifest", repo.baseline)
                instructions = [
                    name
                    for name in git(
                        repo.path, "ls-tree", "-r", "--name-only", repo.commit
                    ).splitlines()
                    if name.rsplit("/", 1)[-1] in {"AGENTS.md", "AGENTS.override.md"}
                ]
                save_record(store, "input", "repository-instructions", instructions)
            store.metadata["repository"]["workspace"] = str(repo.path)
            store.save_metadata()
            engine = Engine(store, case, protocol, result, repo, adapter, config, timeout)
            engine.drive(maximum)
            engine.guard()
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
    result["usage"]["wall_time_seconds"] = round(
        (result["usage"]["wall_time_seconds"] or 0) + time.monotonic() - started, 3
    )
    store.finish(result)
    write_report(store, result)
    return store.path, result


def render_report(result):
    lines = [
        f"# MRAC-Spec: {result['run_id']}",
        "",
        f"Status: {result['status']}",
        f"Phase: {result['flow']['phase']}",
        f"Baseline: {result['flow']['base_head']}",
        f"Spec: {result['final_artifact']}",
    ]
    historical = result["flow"]["schema_version"] == 2
    if historical:
        lines += [
            "Historical adjudicated run (read-only); clean rounds may include rejected/deferred findings.",
            "Start a new run to use direct findings; historical clean counts cannot be carried over.",
        ]
    if result.get("terminal_reason"):
        lines += [result["terminal_reason"]]
    if result.get("frozen_spec_sha256"):
        lines += [
            f"Frozen SHA-256: {result['frozen_spec_sha256']}",
            "Clean audits: " + ", ".join(result["clean_audit_ids"]),
        ]
    for row in result["trajectory"]:
        counts = row.get("accepted_by_severity" if historical else "reported_by_severity", {})
        summary = " ".join(f"{s}x{counts[s]}" for s in ("P0", "P1", "P2", "P3") if counts.get(s))
        lines += ["", f"R{row['audit_round']}, {summary or row['status']}", row["audit_id"]]
        for check in row.get("repository_review", {}).get("checks", []):
            lines.append(f"Evidence: {check['path']}::{check['symbol']} — {check['conclusion']}")
    for finding in result.get("deferred_p3", []):
        lines.append(
            f"Deferred P3 ({finding['audit_id']}): {finding['title']} — {finding['reason']}"
        )
    for question in result["flow"]["questions"]:
        lines.append("Required input: " + question)
    lines += [
        "",
        (
            "Scope: repository-backed Spec design feasibility; no executed builds/tests, "
            "proof of intent entailment, or deployment resource verification."
        ),
    ]
    return "\n".join(lines) + "\n"


def write_report(store, result):
    atomic_text(store.path / "run-report.md", render_report(result))


def read_checkpoint(path, *, allow_historical=False):
    """Check the version before any mutation, including creating a run-lock file."""
    try:
        checkpoint = json.loads((path / "repository-state.json").read_bytes())
        result = checkpoint["result"]
        version = checkpoint["schema_version"]
        if (
            version not in {2, STATE_VERSION}
            or result["workflow"] != WORKFLOW
            or result["flow"]["schema_version"] != version
        ):
            raise BenchError("RESUME_ERROR", "Unsupported state version/workflow; start a new run")
        if version != STATE_VERSION and not allow_historical:
            raise BenchError(
                "RESUME_ERROR",
                "Historical adjudicated runs are read-only; start a new run without old clean counts",
            )
        return checkpoint
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise BenchError("RESUME_ERROR", f"Cannot validate run checkpoint: {exc}") from exc


def load_session(path, *, allow_historical=False):
    """Read the canonical checkpoint without migrating historical clean counts."""
    try:
        checkpoint = read_checkpoint(path, allow_historical=allow_historical)
        version, result = checkpoint["schema_version"], checkpoint["result"]
        if result["flow"]["phase"] not in {"AUDIT", "FIX", "FROZEN", "PAUSED", "ABORTED"}:
            raise BenchError("RESUME_ERROR", "Unknown phase; BLOCKED states cannot be migrated")
        evidence = Evidence(path, checkpoint["evidence_sha256"])
        evidence.check()
        base = object.__new__(RunStore)
        base.path = path
        base.metadata = load_yaml((path / "run.yaml").read_bytes(), "run.yaml")
        base.run_id = result["run_id"]
        snapshots = {
            name[6:]: evidence.path(name).read_bytes()
            for name in evidence.known
            if name.startswith("input/")
        }
        pinned = json.loads(snapshots["execution-config.json"])
        if any(base.metadata[key] != value for key, value in pinned.items()):
            raise BenchError("RESUME_ERROR", "Pinned execution settings changed")
        case = case_from_snapshots(result["case_id"], snapshots)
        protocol = protocol_from_snapshots(result["protocol_id"], snapshots)
        expected_protocol_version = {2} if version == 2 else {2, 3, PROTOCOL_VERSION}
        if (
            protocol.version not in expected_protocol_version
            or result["protocol_version"] != protocol.version
            or pinned["protocol_version"] != protocol.version
        ):
            raise BenchError("RESUME_ERROR", "Protocol/state version mismatch; start a new run")
        if protocol.workflow != WORKFLOW or result["flow"]["base_head"] != case.commit:
            raise BenchError("RESUME_ERROR", "Protocol or baseline changed")
        settings, requested = pinned["effective_config"], pinned["requested_config"]
        maximum = settings["max_audit_rounds"]
        if maximum is not None:
            maximum = positive_int(maximum, "max_audit_rounds")
        timeout = positive_int(settings["agent_timeout_seconds"], "agent_timeout_seconds")
        config = RunConfig(
            Path(requested["project_root"]),
            case.id,
            path.parent,
            Path(requested["workspace_dir"]),
            settings["model"],
            maximum,
            timeout,
            protocol.id,
            settings.get("reasoning_effort"),
            provider=settings.get("provider"),
        )
        store = SessionStore(base, evidence)
        current = read_spec(evidence.path("working/spec.md"))
        if result["flow"]["phase"] == "FROZEN" and digest(current) != result["frozen_spec_sha256"]:
            raise BenchError("FROZEN_SPEC_CHANGED", "Frozen Spec changed; start a new run")
        return store, result, case, protocol, config, current
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise BenchError("RESUME_ERROR", f"Cannot validate run checkpoint: {exc}") from exc


def unfinished_invocation(store, result):
    call = result["flow"]["last_call"]
    if not call:
        return None
    path = store.path / "raw" / call["stage"] / "invocation.json"
    if not path.exists():
        return None
    invocation = json.loads(path.read_bytes())
    if (
        invocation.get("started")
        and not invocation.get("ended_at")
        and pid_alive(invocation.get("pid"))
    ):
        raise BenchError(
            "RESUME_ERROR", "Previous agent process is still alive; end it before resuming"
        )
    return invocation


def reconcile_interruption(store, result, invocation):
    flow = result["flow"]
    call = flow["last_call"]
    if call:
        field = f"{call['role']}_rounds"
        if invocation and invocation.get("started") and result[field] == call["counter_before"]:
            result[field] += 1
            if call["role"] == "audit" and flow["active_audit"]:
                result["trajectory"].append(dict(flow["active_audit"]))
        raw = store.path / "raw" / call["stage"]
        if raw.exists():
            for path in sorted(raw.rglob("*")):
                name = path.relative_to(store.path).as_posix()
                if path.is_file() and name not in store.evidence.known:
                    store.evidence.record(name)
        flow["last_call"] = None
    active = flow["active_audit"]
    if active:
        for row in result["trajectory"]:
            if row["audit_id"] == active["audit_id"]:
                row["status"] = "abandoned"
        flow["active_audit"] = None
    flow["clean"] = []


def resume_repository_run(path, adapter, input_file=None, spec_file=None):
    path = path.resolve()
    if not path.is_dir():
        raise BenchError("RESUME_ERROR", "Run directory does not exist")
    read_checkpoint(path)
    with session_lock(path):
        store, result, case, protocol, config, current = load_session(path)
        check_adapter(adapter, config.provider, config.model, config.reasoning_effort)
        if result["status"] not in RESUMABLE:
            raise BenchError("RESUME_ERROR", f"Cannot resume {result['status']}; start a new run")
        invocation = unfinished_invocation(store, result)
        if (
            adapter.agent_type != result["agent"]["type"]
            or adapter.version() != result["agent"]["version"]
        ):
            raise BenchError("RESUME_ERROR", "Resume requires the original adapter type/version")
        if input_file and result["flow"]["phase"] != "FIX":
            raise BenchError("RESUME_ERROR", "User input is only accepted for pending FIX findings")
        response = read_spec(input_file.resolve()) if input_file else None
        if spec_file:
            current = read_spec(spec_file.resolve())
        if (
            result["status"] == "NEEDS_INPUT"
            and response is None
            and spec_file is None
            and digest(current) == result["flow"]["artifact_sha256"]
        ):
            return path, result
        started = time.monotonic()
        # Do not overwrite a paused/waiting checkpoint when repository preflight fails.
        with prepare_repository(case, config.workspace_dir, path) as repo:
            baseline = "input/repository-manifest.json"
            if (
                baseline in store.evidence.known
                and json.loads(store.evidence.path(baseline).read_bytes()) != repo.baseline
            ):
                raise BenchError("RESUME_ERROR", "Repository differs from original run")
            store.evidence.check()
            flow = result["flow"]
            number = flow["resume_count"] + 1
            save_record(
                store,
                "resumptions",
                f"resume-{number:02d}",
                {"at": utc_now(), "previous_result": result},
            )
            reconcile_interruption(store, result, invocation)
            if digest(current) != flow["artifact_sha256"]:
                result["final_artifact"] = save_artifact(
                    store, f"spec.resume-{number:02d}", current
                )
                store.evidence.path("working/spec.md").write_bytes(current)
                flow["artifact_sha256"] = digest(current)
            if response is not None:
                relative = f"responses/response-{number:02d}.md"
                target = store.evidence.path(relative)
                target.parent.mkdir(exist_ok=True)
                with target.open("xb") as stream:
                    stream.write(response)
                store.evidence.record(relative)
                flow["responses"].append(relative)
            if flow["phase"] == "PAUSED":
                flow.update(phase="AUDIT", failure_streak=0)
            flow["resume_count"] = number
            result.update(status="RUNNING", error=None, terminal_reason=None)
            store.metadata.update(status="RUNNING", ended_at=None)
            store.save_metadata()
            store.checkpoint(result, "resumed")
            return execute(
                config,
                adapter,
                store,
                case,
                protocol,
                result,
                config.max_rounds,
                config.timeout_seconds,
                started,
                prepared_repo=repo,
            )


def inspect_repository_run(path, *, abort_reason=None):
    path = path.resolve()
    if not path.is_dir():
        raise BenchError("RESUME_ERROR", "Run directory does not exist")
    if abort_reason is not None:
        read_checkpoint(path)
    with session_lock(path) if abort_reason is not None else nullcontext():
        store, result, _, _, _, current = load_session(path, allow_historical=abort_reason is None)
        if abort_reason is not None:
            if not abort_reason.strip() or result["flow"]["phase"] in {"FROZEN", "ABORTED"}:
                raise BenchError(
                    "RESUME_ERROR", "Abort needs a nonempty reason and a nonterminal run"
                )
            invocation = unfinished_invocation(store, result)
            save_record(
                store,
                "resumptions",
                f"abort-{result['flow']['resume_count'] + 1:02d}",
                {
                    "at": utc_now(),
                    "reason": abort_reason,
                    "previous_result": result,
                },
            )
            reconcile_interruption(store, result, invocation)
            result["flow"]["phase"] = "ABORTED"
            result.update(status="ABORTED", terminal_reason=abort_reason)
            store.finish(result)
        elif (
            result["flow"]["active_audit"]
            and digest(current) != result["flow"]["active_audit"]["artifact_sha256"]
        ):
            raise BenchError("AUDIT_STALE", "Working Spec changed; resume to abandon the old round")
        if result["status"] != "RUNNING" and result["flow"]["schema_version"] == STATE_VERSION:
            write_report(store, result)
        return path, result
