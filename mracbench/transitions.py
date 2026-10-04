"""Pure protocol state decisions; callers own invocation, evidence and checkpoint order.

Typed dictionaries deliberately keep the existing checkpoint keys and JSON representation.
Each transition returns updates without modifying its inputs.
"""

from dataclasses import dataclass, field
from typing import Required, TypedDict

from .models import BenchError


class CleanObservation(TypedDict, total=False):
    audit_id: Required[str]
    auditor_id: str
    sha256: str
    base_head: str
    candidate_sha256: str
    spec_sha256: str


class PendingFix(TypedDict, total=False):
    audit_id: Required[str]
    findings: list[dict]
    before_sha256: str
    kind: str
    accepted: list[dict]


class FlowState(TypedDict):
    phase: str
    clean: list[CleanObservation]
    pending_fix: PendingFix | None


class SpecFlowState(FlowState):
    artifact_sha256: str
    failure_streak: int


class FlowUpdate(TypedDict, total=False):
    phase: str
    clean: list[CleanObservation]
    pending_fix: PendingFix | None
    active_audit: dict | None
    questions: list[str]
    artifact_sha256: str
    failure_streak: int


class ResultUpdate(TypedDict, total=False):
    status: str
    terminal_reason: str
    convergence_round: int
    frozen_spec_sha256: str
    final_candidate_sha256: str
    clean_audit_ids: list[str]


@dataclass(frozen=True)
class Transition:
    flow: FlowUpdate = field(default_factory=dict)
    result: ResultUpdate = field(default_factory=dict)


def clean_observations(
    previous: list[CleanObservation],
    current: CleanObservation,
    identity: tuple[str, ...],
    *,
    independent: bool = False,
) -> list[CleanObservation]:
    if previous and any(previous[-1][key] != current[key] for key in identity):
        previous = []
    if independent and any(row["auditor_id"] == current["auditor_id"] for row in previous):
        raise BenchError("AUDIT_INVALID", "Auditor identity reused")
    return (previous + [current])[-2:]


def repository_audited(
    flow: SpecFlowState,
    audit_id: str,
    auditor_id: str,
    findings: list[dict],
    base_head: str,
    audit_round: int,
) -> Transition:
    if findings:
        blockers = any(f["severity"] in ("P0", "P1", "P2") for f in findings)
        return Transition(
            {
                "active_audit": None,
                "phase": "FIX",
                "clean": [],
                "questions": [],
                "pending_fix": {
                    "audit_id": audit_id,
                    "findings": findings,
                    "before_sha256": flow["artifact_sha256"],
                },
                "failure_streak": flow["failure_streak"] + 1 if blockers else 0,
            }
        )
    clean = clean_observations(
        flow["clean"],
        {
            "audit_id": audit_id,
            "auditor_id": auditor_id,
            "sha256": flow["artifact_sha256"],
            "base_head": base_head,
        },
        ("sha256", "base_head"),
        independent=True,
    )
    changes: FlowUpdate = {"active_audit": None, "failure_streak": 0, "clean": clean}
    if len(clean) < 2:
        return Transition(changes)
    return Transition(
        {**changes, "phase": "FROZEN"},
        {
            "status": "CONVERGED",
            "convergence_round": audit_round,
            "frozen_spec_sha256": flow["artifact_sha256"],
            "clean_audit_ids": [row["audit_id"] for row in clean],
            "terminal_reason": "Two empty-findings audits at the fixed baseline",
        },
    )


def simple_reviewed(
    flow: SpecFlowState,
    audit_id: str,
    kind: str,
    accepted: list[dict],
) -> Transition:
    if accepted:
        blockers = any(f["severity"] != "P3" for f in accepted)
        changes: FlowUpdate = {
            "clean": [],
            "pending_fix": {
                "audit_id": audit_id,
                "kind": kind,
                "accepted": accepted,
            },
        }
        if kind == "spec-freeze-loop" and blockers:
            changes["failure_streak"] = flow["failure_streak"] + 1
        return Transition(changes)
    if kind != "spec-freeze-loop":
        return Transition({"phase": "spec-freeze-loop"})
    clean = clean_observations(
        flow["clean"],
        {
            "audit_id": audit_id,
            "sha256": flow["artifact_sha256"],
        },
        ("sha256",),
    )
    return Transition({"failure_streak": 0, "clean": clean})


def simple_stop(flow: SpecFlowState, audit_round: int, maximum: int) -> Transition:
    if len(flow["clean"]) == 2:
        return Transition(
            {"phase": "FROZEN"},
            {
                "status": "CONVERGED",
                "convergence_round": audit_round,
                "frozen_spec_sha256": flow["artifact_sha256"],
                "clean_audit_ids": [row["audit_id"] for row in flow["clean"]],
                "terminal_reason": "Two reviewed clean freeze audits on identical Spec bytes",
            },
        )
    if audit_round >= maximum:
        return Transition(
            result={
                "status": "NON_CONVERGED",
                "terminal_reason": "Total audit budget exhausted",
            }
        )
    if flow["failure_streak"] >= 6:
        return Transition(
            result={
                "status": "PAUSED",
                "terminal_reason": "Six accepted P0-P2 freeze finding rounds",
            }
        )
    return Transition()


def exec_audited(
    flow: FlowState,
    audit_id: str,
    auditor_id: str,
    findings: list[dict],
    candidate_sha256: str,
    spec_sha256: str,
    base_head: str,
    audit_round: int,
) -> Transition:
    if findings:
        return Transition(
            {
                "active_audit": None,
                "phase": "FIX",
                "clean": [],
                "pending_fix": {"audit_id": audit_id, "findings": findings},
            }
        )
    clean = clean_observations(
        flow["clean"],
        {
            "audit_id": audit_id,
            "auditor_id": auditor_id,
            "candidate_sha256": candidate_sha256,
            "spec_sha256": spec_sha256,
            "base_head": base_head,
        },
        ("candidate_sha256", "spec_sha256", "base_head"),
    )
    changes: FlowUpdate = {"active_audit": None, "clean": clean}
    if len(clean) < 2:
        return Transition(changes)
    return Transition(
        {**changes, "phase": "CONVERGED"},
        {
            "status": "CONVERGED",
            "convergence_round": audit_round,
            "final_candidate_sha256": candidate_sha256,
            "clean_audit_ids": [row["audit_id"] for row in clean],
            "terminal_reason": "Two zero-finding audits on the same Spec and candidate",
        },
    )


def spec_repaired(artifact_sha256: str, *, repository: bool = False) -> Transition:
    changes: FlowUpdate = {
        "phase": "AUDIT" if repository else "spec-freeze-loop",
        "artifact_sha256": artifact_sha256,
        "pending_fix": None,
        "clean": [],
    }
    if repository:
        changes["questions"] = []
    return Transition(changes)


def exec_stop(audit_round: int, audit_limit: int) -> Transition:
    if audit_round >= audit_limit:
        return Transition(
            result={
                "status": "PAUSED",
                "terminal_reason": "Six-audit batch exhausted; resume adds six",
            }
        )
    return Transition()


def repository_after_repair(
    flow: SpecFlowState,
    audit_round: int,
    maximum: int | None,
) -> Transition:
    if maximum is not None and audit_round >= maximum:
        return Transition(
            result={
                "status": "NON_CONVERGED",
                "terminal_reason": "Explicit audit budget exhausted",
            }
        )
    if flow["failure_streak"] >= 6:
        return Transition(
            {"phase": "PAUSED"},
            {
                "status": "PAUSED",
                "terminal_reason": "Six rounds with P0-P2 findings; repairs recorded",
            },
        )
    return Transition()
