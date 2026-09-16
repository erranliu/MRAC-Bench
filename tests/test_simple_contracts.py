import json

import pytest

from mracbench.models import BenchError
from mracbench.simple_audit import assign_ids, parse_findings, parse_repair, parse_review

FINDINGS = [{"finding_id": "F1", "severity": "P1", "title": "Gap", "evidence": "Spec §2"}]


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
def test_repair_requires_all_closures_and_no_partial_block(change):
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
