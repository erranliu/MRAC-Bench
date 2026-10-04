import copy

import pytest

from mracbench.models import BenchError
from mracbench.transitions import (
    clean_observations,
    exec_audited,
    exec_stop,
    repository_after_repair,
    repository_audited,
    simple_reviewed,
    simple_stop,
    spec_repaired,
)


def state(**changes):
    return {
        "phase": "AUDIT",
        "artifact_sha256": "spec",
        "failure_streak": 0,
        "pending_fix": None,
        "clean": [],
        **changes,
    }


def finding(severity):
    return {"finding_id": "F1", "severity": severity}


@pytest.mark.parametrize("severity,streak", [("P0", 6), ("P1", 6), ("P2", 6), ("P3", 0)])
def test_repository_findings_preserve_all_fixes_and_reset_p3_streak(severity, streak):
    flow = state(failure_streak=5, clean=[{"audit_id": "old"}])
    before = copy.deepcopy(flow)
    transition = repository_audited(flow, "audit", "auditor", [finding(severity)], "base", 6)
    assert flow == before
    assert transition.flow["failure_streak"] == streak
    assert transition.flow["clean"] == []
    assert transition.flow["pending_fix"]["findings"] == [finding(severity)]
    assert transition.flow["phase"] == "FIX" and not transition.result


def test_repository_convergence_requires_same_spec_baseline_and_independent_auditor():
    flow = state()
    first = repository_audited(flow, "a1", "r1", [], "base", 1)
    flow.update(first.flow)
    second = repository_audited(flow, "a2", "r2", [], "base", 2)
    assert second.result["status"] == "CONVERGED"
    assert second.result["clean_audit_ids"] == ["a1", "a2"]
    for changed in ({"artifact_sha256": "new"},):
        reset = repository_audited({**flow, **changed}, "a2", "r2", [], "base", 2)
        assert len(reset.flow["clean"]) == 1 and not reset.result
    assert len(repository_audited(flow, "a2", "r2", [], "new-base", 2).flow["clean"]) == 1
    with pytest.raises(BenchError, match="Auditor identity reused"):
        repository_audited(flow, "a2", "r1", [], "base", 2)


@pytest.mark.parametrize("maximum,status", [(6, "NON_CONVERGED"), (7, "PAUSED"), (None, "PAUSED")])
def test_repository_pause_decision_is_after_repair_and_hard_cap_wins(maximum, status):
    flow = state(failure_streak=6)
    before = copy.deepcopy(flow)
    decision = repository_after_repair(flow, 6, maximum)
    assert flow == before
    assert decision.result["status"] == status


def test_simple_init_clean_is_not_a_freeze_clean_and_p3_keeps_existing_failure_streak():
    flow = state(phase="spec-init", failure_streak=5)
    before = copy.deepcopy(flow)
    init = simple_reviewed(flow, "init", "spec-init", [])
    assert init.flow == {"phase": "spec-freeze-loop"}
    p3 = simple_reviewed(flow, "freeze", "spec-freeze-loop", [finding("P3")])
    assert "failure_streak" not in p3.flow
    assert p3.flow["pending_fix"]["accepted"] == [finding("P3")]
    assert flow == before


def test_simple_clean_resets_failure_streak_and_convergence_precedes_budget():
    flow = state(phase="spec-freeze-loop", failure_streak=6)
    flow.update(simple_reviewed(flow, "a1", flow["phase"], []).flow)
    assert flow["failure_streak"] == 0
    flow.update(simple_reviewed(flow, "a2", flow["phase"], []).flow)
    assert simple_stop(flow, 2, 2).result["status"] == "CONVERGED"
    assert simple_stop(state(failure_streak=6), 6, 6).result["status"] == "NON_CONVERGED"
    assert simple_stop(state(failure_streak=6), 6, 7).result["status"] == "PAUSED"


@pytest.mark.parametrize("changed", ["candidate_sha256", "spec_sha256", "base_head"])
def test_exec_clean_is_bound_to_all_three_identities(changed):
    flow = state()
    flow.update(exec_audited(flow, "a1", "r1", [], "candidate", "spec", "base", 1).flow)
    before = copy.deepcopy(flow)
    identity = {"candidate_sha256": "candidate", "spec_sha256": "spec", "base_head": "base"}
    same = exec_audited(flow, "a2", "r2", [], **identity, audit_round=2)
    assert same.result["status"] == "CONVERGED"
    identity[changed] = "changed"
    reset = exec_audited(flow, "a2", "r2", [], **identity, audit_round=2)
    assert len(reset.flow["clean"]) == 1 and not reset.result
    assert flow == before


def test_exec_p3_enters_fix_and_repair_clears_clean():
    transition = exec_audited(state(), "a", "r", [finding("P3")], "c", "s", "b", 1)
    assert transition.flow["phase"] == "FIX"
    assert transition.flow["pending_fix"]["findings"] == [finding("P3")]
    assert spec_repaired("new").flow == {
        "phase": "spec-freeze-loop",
        "pending_fix": None,
        "clean": [],
        "artifact_sha256": "new",
    }
    assert spec_repaired("new", repository=True).flow["questions"] == []


@pytest.mark.parametrize("audit_round,limit,paused", [(5, 6, False), (6, 6, True), (6, 12, False)])
def test_exec_budget_pause_keeps_pending_findings_and_first_clean(audit_round, limit, paused):
    decision = exec_stop(audit_round, limit)
    assert not decision.flow
    assert bool(decision.result) == paused
    if paused:
        assert decision.result["status"] == "PAUSED"


def test_clean_history_is_bounded_and_input_is_not_modified():
    previous = [{"audit_id": "a1", "sha256": "s"}, {"audit_id": "a2", "sha256": "s"}]
    before = copy.deepcopy(previous)
    clean = clean_observations(previous, {"audit_id": "a3", "sha256": "s"}, ("sha256",))
    assert [row["audit_id"] for row in clean] == ["a2", "a3"]
    assert previous == before
