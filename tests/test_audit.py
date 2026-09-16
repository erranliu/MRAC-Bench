import json

import pytest
from conftest import BLOCKING, CLEAN, ISSUE

from mracbench.audit import Convergence, blocking_count, parse_audit, parse_spec
from mracbench.models import BenchError


def test_valid_audits_and_nonblocking():
    assert blocking_count(parse_audit(CLEAN)) == 0
    assert blocking_count(parse_audit(BLOCKING)) == 1
    note = {**ISSUE, "severity": "non_blocking"}
    assert blocking_count(parse_audit(json.dumps({"status": "clean", "issues": [note]}))) == 0


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "```json\n" + CLEAN + "\n```",
        CLEAN + " explanation",
        "[]",
        "null",
        '{"status":"clean","status":"clean","issues":[]}',
        '{"status":"clean","issues":[NaN]}',
        json.dumps({"status": "clean", "issues": [ISSUE]}),
        json.dumps({"status": "issues_found", "issues": []}),
        json.dumps({"status": "issues_found", "issues": [ISSUE, ISSUE]}),
        json.dumps({"status": "issues_found", "issues": [{**ISSUE, "evidence": ""}]}),
        json.dumps({"status": "issues_found", "issues": [{**ISSUE, "severity": "critical"}]}),
    ],
)
def test_invalid_audit_never_becomes_clean(raw):
    with pytest.raises(BenchError, match="Invalid audit JSON"):
        parse_audit(raw)


def test_convergence_resets():
    state = Convergence()
    assert not state.observe(0)
    assert not state.observe(1)
    assert not state.observe(0)
    assert state.observe(0)


@pytest.mark.parametrize("raw", ["", "# Title", "```md\n# title\nbody\n```", "diff --git a/x b/x"])
def test_bad_spec(raw):
    with pytest.raises(BenchError):
        parse_spec(raw)
