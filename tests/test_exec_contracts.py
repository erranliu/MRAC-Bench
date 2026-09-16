import json

import pytest

from mracbench.exec_audit import parse_code_result, parse_exec_audit
from mracbench.models import BenchError


@pytest.mark.parametrize(
    "change",
    [
        {"candidate_sha256": "wrong"},
        {"spec_sha256": "wrong"},
        {"audit_id": "wrong"},
        {"findings": None},
        {"findings": [{"severity": "non_blocking", "title": "x", "evidence": "y"}]},
        {"findings": [{"severity": "P1", "title": "x", "evidence": ""}]},
        {"status": "clean"},
    ],
)
def test_exec_audit_strict_identity_and_issue_schema(change):
    data = {"audit_id": "A", "spec_sha256": "S", "candidate_sha256": "C", "findings": []}
    with pytest.raises(BenchError) as exc:
        parse_exec_audit(json.dumps({**data, **change}), "A", "S", "C")
    assert exc.value.kind == "AUDIT_INVALID"


@pytest.mark.parametrize(
    "change",
    [
        {"validation": []},
        {"fixes": []},
        {"questions": ["not complete"]},
        {"fixes": [{"finding_id": "F1", "summary": "x", "evidence": ""}]},
        {"fixes": [{"finding_id": "F1", "summary": "x", "evidence": "y"}] * 2},
    ],
)
def test_repairs_cannot_skip_findings_or_verification_records(change):
    data = {
        "disposition": "complete",
        "summary": "Fixed",
        "validation": [{"command": "test", "status": "not_run", "evidence": "missing fixture"}],
        "fixes": [{"finding_id": "F1", "summary": "x", "evidence": "y"}],
    }
    with pytest.raises(BenchError) as exc:
        parse_code_result(json.dumps({**data, **change}), [{"finding_id": "F1"}])
    assert exc.value.kind == "FIX_INVALID"
