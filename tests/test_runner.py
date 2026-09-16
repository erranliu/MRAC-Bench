import json
from dataclasses import replace

import pytest
from conftest import BLOCKING, CLEAN, SPEC, StubAgent, git
from test_inputs import change_case

from mracbench.models import AgentResult
from mracbench.runner import run_case


def test_complete_repair_loop_and_audit_independence(config):
    agent = StubAgent([SPEC, BLOCKING, SPEC + "\nCorrection.\n", CLEAN, CLEAN])
    path, result = run_case(config, agent)
    assert result["status"] == "CONVERGED"
    assert result["convergence_round"] == result["audit_rounds"] == 3
    assert result["repair_rounds"] == 1
    assert (path / "artifacts" / "spec.initial.md").read_text() == SPEC
    assert len(list((path / "artifacts").iterdir())) == 2
    assert [r.raw_dir.name for r in agent.requests] == [
        "generate",
        "audit-01",
        "repair-01",
        "audit-02",
        "audit-03",
    ]
    assert agent.requests[3].prompt == agent.requests[4].prompt
    for request in agent.requests:
        assert "METADATA_MUST_NOT_LEAK" not in request.prompt
        payload = json.loads(request.prompt.split("INPUT JSON:\n", 1)[1])
        assert ("current_audit" in payload) == request.raw_dir.name.startswith("repair")
    assert json.loads((path / "result.json").read_text())["trajectory"] == result["trajectory"]
    for request in agent.requests:
        for name in (
            "request.txt",
            "stdout.txt",
            "stderr.txt",
            "execution.json",
            "repository-after.json",
        ):
            assert (request.raw_dir / name).exists()
    assert all(
        not json.loads((r.raw_dir / "repository-after.json").read_text())["violation"]
        for r in agent.requests
    )


@pytest.mark.parametrize(
    "replies,maximum,status,audits,repairs",
    [
        ([SPEC, CLEAN, CLEAN], 2, "CONVERGED", 2, 0),
        ([SPEC, CLEAN], 1, "NON_CONVERGED", 1, 0),
        ([SPEC, BLOCKING], 1, "NON_CONVERGED", 1, 0),
        ([SPEC, CLEAN, BLOCKING, SPEC, CLEAN, CLEAN], 4, "CONVERGED", 4, 1),
        ([SPEC, BLOCKING, SPEC, BLOCKING], 2, "NON_CONVERGED", 2, 1),
    ],
)
def test_round_boundaries(config, replies, maximum, status, audits, repairs):
    _, result = run_case(replace(config, max_rounds=maximum), StubAgent(replies))
    assert (result["status"], result["audit_rounds"], result["repair_rounds"]) == (
        status,
        audits,
        repairs,
    )
    assert (result["convergence_round"] is None) == (status != "CONVERGED")


@pytest.mark.parametrize(
    "failure,expected",
    [
        ("not json", "PARSE_ERROR"),
        (AgentResult(started=True, exit_code=1, error_type="AGENT_ERROR"), "AGENT_ERROR"),
        (AgentResult(started=True, error_type="TIMEOUT"), "TIMEOUT"),
    ],
)
def test_failed_audit_has_trajectory_and_keeps_artifacts(config, failure, expected):
    path, result = run_case(config, StubAgent([SPEC, failure]))
    assert result["status"] == expected
    assert result["audit_rounds"] == 1
    assert result["trajectory"][0]["status"] == expected
    assert result["trajectory"][0]["blocking_issue_count"] is None
    assert (path / "artifacts" / "spec.initial.md").exists()
    assert not (path / "audits" / "audit-01.json").exists()


def test_unstarted_audit_is_not_counted(config):
    agent = StubAgent([SPEC, AgentResult(error_type="AGENT_ERROR")])
    _, result = run_case(config, agent)
    assert result["status"] == "AGENT_ERROR"
    assert result["audit_rounds"] == 0
    assert result["trajectory"] == []


def test_failed_repair_preserves_previous_spec_and_audit(config):
    path, result = run_case(config, StubAgent([SPEC, BLOCKING, ""]))
    assert result["status"] == "PARSE_ERROR"
    assert result["repair_rounds"] == 1
    assert result["trajectory"][0]["status"] == "issues_found"
    assert result["trajectory"][0]["repair_artifact"] is None
    assert len(list((path / "artifacts").iterdir())) == 1
    assert (path / "audits" / "audit-01.json").exists()


@pytest.mark.parametrize("kind", ["tracked", "untracked", "ignored", "committed"])
def test_repository_changes_are_violations(config, kind):
    def mutate(request):
        if kind in ("tracked", "committed"):
            (request.workspace / "app.py").write_text("value = 2\n")
        else:
            (request.workspace / ("ignored.txt" if kind == "ignored" else "extra.txt")).write_text(
                "change"
            )
        if kind == "committed":
            git(request.workspace, "config", "user.email", "tests@example.invalid")
            git(request.workspace, "config", "user.name", "tests")
            git(request.workspace, "add", ".")
            git(request.workspace, "commit", "--quiet", "-m", "unexpected")
        return CLEAN

    path, result = run_case(config, StubAgent([SPEC, mutate]))
    assert result["status"] == "PROTOCOL_VIOLATION"
    assert result["protocol_violation"] is True
    assert result["trajectory"][0]["status"] == "PROTOCOL_VIOLATION"
    assert json.loads((path / "raw" / "audit-01" / "repository-after.json").read_text())[
        "violation"
    ]
    # Never reset or erase the evidence when the dirty cache is reused.
    _, repeat = run_case(config, StubAgent([]))
    assert repeat["status"] == "REPOSITORY_ERROR"


def test_missing_commit_is_repository_error(project, config):
    change_case(project, lambda d: d["repository"].update(commit="a" * 40))
    _, result = run_case(config, StubAgent([]))
    assert result["status"] == "REPOSITORY_ERROR"


def test_repeat_runs_are_distinct_and_cache_stays_clean(config):
    first, _ = run_case(config, StubAgent([SPEC, CLEAN, CLEAN]))
    second, result = run_case(config, StubAgent([SPEC, CLEAN, CLEAN]))
    assert first != second
    assert result["status"] == "CONVERGED"
