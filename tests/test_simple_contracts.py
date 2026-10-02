import json

import pytest

from mracbench.models import BenchError
from mracbench.simple_audit import assign_ids, parse_findings, parse_repair, parse_review

FINDINGS = [{"finding_id": "F1", "severity": "P1", "title": "Gap", "evidence": "Spec §2"}]


def test_review_reason_length_limit_is_optional_for_v15():
    reason = "Counter-evidence: " + "x" * 600
    text = json.dumps(
        {"decisions": [{"finding_id": "F1", "outcome": "rejected", "reason": reason}]}
    )
    with pytest.raises(BenchError, match="concise"):
        parse_review(text, "A", FINDINGS, bind_invocation=True)
    review, accepted, deferred = parse_review(
        text, "A", FINDINGS, bind_invocation=True, max_reason_chars=None
    )
    assert review["decisions"][0]["reason"] == reason
    assert accepted == deferred == []


@pytest.mark.parametrize("reason", ["", " ", "first\nsecond", "extra  spaces"])
def test_v15_keeps_nonempty_and_single_line_reason_rules(reason):
    text = json.dumps(
        {"decisions": [{"finding_id": "F1", "outcome": "rejected", "reason": reason}]}
    )
    with pytest.raises(BenchError):
        parse_review(text, "A", FINDINGS, bind_invocation=True, max_reason_chars=None)


def test_invocation_binding_removes_model_audit_id_echo():
    audit = parse_findings('{"findings":[]}', "controller-owned-id", bind_invocation=True)
    assert audit == {"audit_id": "controller-owned-id", "findings": []}
    review, accepted, deferred = parse_review(
        '{"decisions":[{"finding_id":"F1","outcome":"accepted"}]}',
        "controller-owned-id",
        FINDINGS,
        bind_invocation=True,
    )
    assert review["audit_id"] == "controller-owned-id"
    assert accepted == FINDINGS
    assert deferred == []


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"audit_id":"A","findings":[NaN]}',
        '{"audit_id":"A","audit_id":"A","findings":[]}',
        json.dumps({"audit_id": "wrong", "findings": []}),
        json.dumps(
            {"audit_id": "A", "findings": [{"severity": "blocking", "title": "x", "evidence": "y"}]}
        ),
        json.dumps(
            {"audit_id": "A", "findings": [{"severity": "P1", "title": "x", "evidence": ""}]}
        ),
        json.dumps({"audit_id": "A", "findings": [], "status": "clean"}),
    ],
)
def test_bad_findings_are_never_clean(text):
    with pytest.raises(BenchError) as exc:
        parse_findings(text, "A")
    assert exc.value.kind == "PARSE_ERROR"


@pytest.mark.parametrize(
    "decisions",
    [
        [],
        [{"finding_id": "F2", "outcome": "accepted"}],
        [{"finding_id": "F1", "outcome": "accepted"}] * 2,
        [{"finding_id": "F1", "outcome": "deferred"}],
        [{"finding_id": "F1", "outcome": "rejected"}],
        [{"finding_id": "F1", "outcome": "rejected", "reason": " "}],
        [
            {
                "finding_id": "F1",
                "outcome": "rejected",
                "reason": "x",
                "exception": "product-decision",
            }
        ],
        [{"finding_id": "F1", "outcome": "accepted", "exception": "invented"}],
        [{"finding_id": "F1", "outcome": "accepted", "reason": "not supported"}],
    ],
)
def test_bad_review_cannot_hide_or_skip_a_finding(decisions):
    with pytest.raises(BenchError) as exc:
        parse_review(json.dumps({"audit_id": "A", "decisions": decisions}), "A", FINDINGS)
    assert exc.value.kind == "PARSE_ERROR"


@pytest.mark.parametrize(
    "change",
    [
        {"fixes": []},
        {"fixes": [{"finding_id": "F1", "summary": "x", "evidence": ""}]},
        {"fixes": [{"finding_id": "F1", "summary": "x", "evidence": "y"}] * 2},
        {"spec": None},
        {"spec": "# Title only"},
        {"audit_id": "wrong"},
        {"disposition": "block", "reason": "No authority"},
    ],
)
def test_repair_requires_all_closures_and_continue(change):
    data = {
        "audit_id": "A",
        "disposition": "continue",
        "spec": "# Spec\nBody",
        "fixes": [{"finding_id": "F1", "summary": "x", "evidence": "y"}],
    }
    with pytest.raises(BenchError) as exc:
        parse_repair(json.dumps({**data, **change}), "A", FINDINGS)
    assert exc.value.kind == "PARSE_ERROR"


def test_assign_ids_preserves_auditor_findings():
    audit = {"audit_id": "A", "findings": [{"severity": "P2", "title": "x", "evidence": "y"}]}
    original = json.dumps(audit)
    assert assign_ids(audit)[0]["finding_id"] == "F1"
    assert json.dumps(audit) == original
