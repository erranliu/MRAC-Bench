import hashlib
import json
from dataclasses import replace

import pytest
import yaml
from conftest import StubAgent
from test_inputs import change_case

from mracbench.cases import load_case
from mracbench.evidence import run_lock
from mracbench.machine import inspect
from mracbench.models import AgentResult, BenchError
from mracbench.repository import prepare_repository
from mracbench.runner import run_case
from mracbench.simple_flow import resume_run


def payload(request):
    return json.loads(request.prompt.split("INPUT JSON:\n", 1)[1])


def audit(severity=None):
    def reply(request):
        data = payload(request)
        return json.dumps(
            {
                "audit_id": data["audit_id"],
                "findings": []
                if severity is None
                else [
                    {
                        "severity": severity,
                        "title": "Missing failure outcome",
                        "evidence": "Current Spec's failure clause leaves the returned value undefined.",
                    }
                ],
            }
        )

    return reply


def review(outcome="accepted", exception=None):
    def reply(request):
        data = payload(request)
        decisions = []
        for finding in data["findings"]:
            decision = {"finding_id": finding["finding_id"], "outcome": outcome}
            if exception:
                decision["exception"] = exception
            if outcome == "rejected":
                decision["reason"] = "Spec acceptance §3 already requires a null result on failure."
            decisions.append(decision)
        return json.dumps({"audit_id": data["audit_id"], "decisions": decisions})

    return reply


def repair(request):
    data = payload(request)
    return json.dumps(
        {
            "audit_id": data["audit_id"],
            "disposition": "continue",
            "spec": "# Revised Spec\n\n" + data["current_spec"] + "\nFailure returns null.\n",
            "fixes": [
                {
                    "finding_id": finding["finding_id"],
                    "summary": "Defined failure output.",
                    "evidence": "Source acceptance and fixed app.py entail the null result.",
                }
                for finding in data["accepted_findings"]
            ],
        }
    )


def clean():
    return [audit(), review()]


def six_failures():
    replies = clean()
    for index in range(6):
        replies += [audit("P1"), review()]
        if index < 5:
            replies.append(repair)
    return replies


@pytest.fixture
def simple_config(config):
    return replace(config, protocol_id="spec-flow-simple-v1", reasoning_effort="high")


def test_copied_bytes_and_distinct_stage_inputs(project, simple_config):
    original = b"\xef\xbb\xbf# Input Spec\r\n\r\nFailure returns null.\r\n"
    (project / "cases/sample/task.md").write_bytes(original)
    change_case(project, lambda d: d["task"].update(sha256=hashlib.sha256(original).hexdigest()))
    agent = StubAgent(clean() + clean() + clean())
    path, result = run_case(simple_config, agent)
    assert result["status"] == "CONVERGED", result["error"]
    assert result["audit_rounds"] == 3
    assert result["review_rounds"] == 3
    assert result["repair_rounds"] == 0
    assert result["frozen_spec_sha256"] == hashlib.sha256(original).hexdigest()
    assert len(set(result["clean_audit_ids"])) == 2
    assert all("spec-freeze-loop" in aid for aid in result["clean_audit_ids"])
    assert (path / result["final_artifact"]).read_bytes() == original
    assert [row["clean_streak"] for row in result["trajectory"]] == [0, 1, 2]
    metadata = yaml.safe_load((path / "run.yaml").read_text())
    assert "case_default_protocol" not in metadata
    assert metadata["protocol_selection"] == "explicit"
    assert metadata["protocol_id"] == "spec-flow-simple-v1"
    for request in agent.requests:
        assert request.reasoning_effort == "high"
        data = payload(request)
        assert "METADATA_MUST_NOT_LEAK" not in request.prompt
        if request.raw_dir.name.startswith("repository-read-check"):
            assert set(data) == {"repository_path", "fixed_repository_head"}
            assert request.workspace == agent.requests[0].workspace
        elif request.raw_dir.name.startswith("spec-freeze-loop"):
            assert set(data) == {"current_spec", "audit_id", "spec_sha256"}
            assert request.workspace != agent.requests[0].workspace
            assert not list(request.workspace.iterdir())
            assert request.skip_git_repo_check
        elif request.raw_dir.name.startswith("spec-init"):
            assert "source_spec" in data and "fixed_repository_head" in data
    assert (project / "cases/sample/task.md").read_bytes() == original


def test_initial_repair_goes_directly_to_freeze(simple_config):
    agent = StubAgent([audit("P1"), review(), repair] + clean() + clean())
    path, result = run_case(simple_config, agent)
    assert result["status"] == "CONVERGED", result["error"]
    assert [r.raw_dir.name for r in agent.requests] == [
        "repository-read-check-01",
        "spec-init-01",
        "review-01",
        "repair-01",
        "spec-freeze-loop-02",
        "review-02",
        "spec-freeze-loop-03",
        "review-03",
    ]
    assert result["repair_rounds"] == 1
    assert (path / "repairs/repair-01.json").exists()
    assert result["flow"]["failure_streak"] == 0


def test_repair_resets_freeze_streak(simple_config):
    replies = clean() + clean() + [audit("P2"), review(), repair] + clean() + clean()
    _, result = run_case(simple_config, StubAgent(replies))
    assert result["status"] == "CONVERGED", result["error"]
    assert [row["clean_streak"] for row in result["trajectory"]] == [0, 1, 0, 1, 2]
    assert result["convergence_round"] == 5


@pytest.mark.parametrize("severity,outcome", [("P1", "rejected"), ("P3", "deferred")])
def test_review_determines_clean_not_raw_findings(simple_config, severity, outcome):
    replies = clean() + [audit(severity), review(outcome), audit(severity), review(outcome)]
    _, result = run_case(simple_config, StubAgent(replies))
    assert result["status"] == "CONVERGED", result["error"]
    assert result["repair_rounds"] == 0
    assert result["trajectory"][1]["reported_count"] == 1
    assert result["trajectory"][1]["accepted_count"] == 0
    assert len(result["deferred_p3"]) == (2 if outcome == "deferred" else 0)


def test_accepted_p3_requires_repair(simple_config):
    _, result = run_case(
        simple_config, StubAgent(clean() + [audit("P3"), review(), repair] + clean() + clean())
    )
    assert result["status"] == "CONVERGED", result["error"]
    assert result["repair_rounds"] == 1
    assert result["trajectory"][1]["blocking_issue_count"] == 0
    assert result["trajectory"][1]["clean_streak"] == 0


@pytest.mark.parametrize(
    "exception", ["product-decision", "scope-expansion", "external-dependency"]
)
def test_removed_review_exception_is_invalid_not_blocked(simple_config, exception):
    path, result = run_case(simple_config, StubAgent([audit("P1"), review(exception=exception)]))
    assert result["status"] == "PARSE_ERROR", result["error"]
    assert result["flow"]["clean"] == []
    assert result["repair_rounds"] == 0
    before = (path / "result.json").read_bytes()
    with pytest.raises(BenchError, match="Only PAUSED"):
        resume_run(path, StubAgent([]))
    assert (path / "result.json").read_bytes() == before


def test_removed_block_disposition_is_invalid_and_preserves_pending_findings(simple_config):
    def block(request):
        return json.dumps(
            {
                "audit_id": payload(request)["audit_id"],
                "disposition": "block",
                "reason": "Two product outcomes are equally consistent with intent.",
            }
        )

    path, result = run_case(simple_config, StubAgent([audit("P1"), review(), block]))
    assert result["status"] == "PARSE_ERROR", result["error"]
    assert result["flow"]["pending_fix"]["accepted"]
    assert result["flow"]["clean"] == []
    assert len(list((path / "artifacts").iterdir())) == 1
    assert result["repair_rounds"] == 1


def test_ambiguity_is_repaired_then_independently_audited(project, simple_config):
    source = b"# Spec\nDefine the failure result. Two outcomes need a decision.\n"
    (project / "cases/sample/task.md").write_bytes(source)
    change_case(project, lambda d: d["task"].update(sha256=hashlib.sha256(source).hexdigest()))
    agent = StubAgent([audit("P1"), review(), repair] + clean() + clean())
    path, result = run_case(simple_config, agent)
    assert result["status"] == "CONVERGED", result["error"]
    assert result["protocol_version"] == 4
    assert result["flow"]["schema_version"] == 3
    assert result["repair_rounds"] == 1
    assert (path / "input/task.md").read_bytes() == source


@pytest.mark.parametrize("status", ["PAUSED", "BLOCKED"])
def test_historical_simple_results_are_read_only(simple_config, status):
    path, result = run_case(replace(simple_config, max_rounds=12), StubAgent(six_failures()))
    result["flow"]["schema_version"] = 1
    result["protocol_version"] = 1
    result["status"] = status
    raw = json.dumps(result).encode()
    (path / "result.json").write_bytes(raw)
    metadata = yaml.safe_load((path / "run.yaml").read_text())
    metadata["result_sha256"] = hashlib.sha256(raw).hexdigest()
    (path / "run.yaml").write_text(yaml.safe_dump(metadata))
    agent = StubAgent([])
    with pytest.raises(BenchError, match="Historical Simple runs are read-only"):
        resume_run(path, agent)
    assert not agent.requests
    assert (path / "result.json").read_bytes() == raw


def test_old_simple_protocol_cannot_start_with_removed_contract(project, simple_config):
    protocol_path = project / "protocols/spec-flow-simple-v1/protocol.yaml"
    data = yaml.safe_load(protocol_path.read_text())
    data["version"] = 1
    protocol_path.write_text(yaml.safe_dump(data))
    agent = StubAgent([])
    _, result = run_case(simple_config, agent)
    assert result["status"] == "CASE_ERROR"
    assert not agent.requests


@pytest.mark.parametrize("schema,protocol_version,actions", [(1, 1, []), (3, 4, ["continue"])])
def test_machine_only_offers_continue_for_current_simple_schema(
    tmp_path, schema, protocol_version, actions
):
    path = tmp_path / "run-simple"
    path.mkdir()
    (path / "lifecycle.json").write_text(
        json.dumps({"schema_version": 1, "run_id": path.name, "lifecycle": "PAUSED"})
    )
    (path / "result.json").write_text(
        json.dumps(
            {
                "protocol_id": "spec-flow-simple-v1",
                "protocol_version": protocol_version,
                "status": "PAUSED",
                "flow": {"schema_version": schema},
            }
        )
    )
    status = inspect(path)
    assert status["checkpoint_valid"]
    assert status["allowed_actions"] == actions


def test_pause_preserves_pending_repair_and_resume_uses_snapshots(project, simple_config):
    # Historical input snapshots may still carry a case-level protocol hint; it is inert.
    change_case(project, lambda d: d.update(protocol={"id": "unavailable-old-protocol"}))
    config = replace(simple_config, max_rounds=12)
    path, paused = run_case(config, StubAgent(six_failures()))
    assert paused["status"] == "PAUSED", paused["error"]
    assert (paused["audit_rounds"], paused["repair_rounds"]) == (7, 5)
    assert paused["flow"]["pending_fix"]
    originals = {
        p: p.read_bytes()
        for folder in ("input", "artifacts", "audits", "reviews", "raw")
        for p in (path / folder).rglob("*")
        if p.is_file()
    }
    (project / "cases/sample/task.md").write_text("CHANGED live case")
    (project / "protocols/spec-flow-simple-v1/spec-freeze-loop.md").write_text(
        "CHANGED live prompt"
    )
    agent = StubAgent([repair] + clean() + clean())
    resumed_path, result = resume_run(path, agent)
    assert resumed_path == path
    assert result["status"] == "CONVERGED", result["error"]
    assert (result["audit_rounds"], result["repair_rounds"]) == (9, 6)
    assert result["review_rounds"] == 9
    assert result["flow"]["resume_count"] == 1
    assert agent.requests[1].raw_dir.name == "repair-06"
    assert all("CHANGED live" not in request.prompt for request in agent.requests)
    assert all(request.reasoning_effort == "high" for request in agent.requests)
    assert all(p.read_bytes() == content for p, content in originals.items())
    saved = json.loads((path / "resumptions/resume-01.json").read_text())
    assert saved["previous_result"]["status"] == "PAUSED"


def test_clean_resets_six_round_failure_counter(simple_config):
    replies = clean()
    for _ in range(5):
        replies += [audit("P1"), review(), repair]
    replies += clean() + [audit("P1"), review(), repair] + clean() + clean()
    _, result = run_case(replace(simple_config, max_rounds=12), StubAgent(replies))
    assert result["status"] == "CONVERGED", result["error"]
    assert result["flow"]["resume_count"] == 0


@pytest.mark.parametrize(
    "target",
    [
        "artifacts/spec.initial.md",
        "input/spec-freeze-loop.md",
        "reviews/spec-init-01.json",
        "raw/spec-init-01/final.txt",
        "result.json",
        "run.yaml",
    ],
)
def test_tampered_paused_run_is_not_resumed(simple_config, target):
    path, result = run_case(replace(simple_config, max_rounds=12), StubAgent(six_failures()))
    assert result["status"] == "PAUSED", result["error"]
    file = path / target
    if target == "run.yaml":
        metadata = yaml.safe_load(file.read_text())
        metadata["effective_config"]["model"] = "substituted-model"
        file.write_text(yaml.safe_dump(metadata))
    else:
        file.write_bytes(file.read_bytes() + b"changed")
    before = (path / "result.json").read_bytes()
    agent = StubAgent([])
    with pytest.raises(BenchError):
        resume_run(path, agent)
    assert not agent.requests
    assert (path / "result.json").read_bytes() == before
    assert not (path / ".run.lock").exists()


def test_total_budget_is_not_extended_by_resume(simple_config):
    path, result = run_case(simple_config, StubAgent(six_failures()))
    assert result["status"] == "PAUSED", result["error"]
    _, result = resume_run(path, StubAgent([repair] + clean()))
    assert result["status"] == "NON_CONVERGED"
    assert result["audit_rounds"] == 8
    assert len(result["flow"]["clean"]) == 1


def test_busy_locks_and_dirty_checkout_preserve_paused_result(simple_config):
    path, result = run_case(replace(simple_config, max_rounds=12), StubAgent(six_failures()))
    assert result["status"] == "PAUSED", result["error"]
    before = (path / "result.json").read_bytes()
    agent = StubAgent([])
    with run_lock(path):
        with pytest.raises(BenchError, match="Run is locked"):
            resume_run(path, agent)
        assert (path / ".run.lock").exists()
    assert (path / "result.json").read_bytes() == before
    case = load_case(simple_config.project_root, simple_config.case_id)
    with prepare_repository(case, simple_config.workspace_dir, path) as repo:
        with pytest.raises(BenchError, match="Checkout is locked"):
            resume_run(path, agent)
        assert (path / "result.json").read_bytes() == before
    (repo.path / "app.py").write_text("dirty checkout")
    with pytest.raises(BenchError, match="dirty"):
        resume_run(path, agent)
    assert (path / "result.json").read_bytes() == before
    assert not (path / "resumptions").exists()
    assert not agent.requests


def test_related_specs_reach_init_review_and_repair_but_never_freeze(project, simple_config):
    raw = b"# Related\nExisting owner is Alpha.\n"
    (project / "cases/sample/related.md").write_bytes(raw)
    change_case(
        project,
        lambda d: d.update(
            related_specs=[
                {
                    "file": "related.md",
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            ]
        ),
    )
    agent = StubAgent([audit("P1"), review(), repair] + clean() + clean())
    path, result = run_case(simple_config, agent)
    assert result["status"] == "CONVERGED", result["error"]
    assert (path / "input/related-spec-01.md").read_bytes() == raw
    for request in agent.requests:
        data = payload(request)
        if request.raw_dir.name.startswith("repository-read-check"):
            assert set(data) == {"repository_path", "fixed_repository_head"}
        elif request.raw_dir.name.startswith("spec-freeze-loop"):
            assert "related_specs" not in data
        else:
            assert data["related_specs"][0]["content"] == raw.decode()


def test_hard_budget_precedes_six_round_pause(simple_config):
    _, result = run_case(replace(simple_config, max_rounds=7), StubAgent(six_failures()))
    assert result["status"] == "NON_CONVERGED", result["error"]
    assert result["repair_rounds"] == 5


@pytest.mark.parametrize("maximum,replies", [(1, clean()), (2, clean() + clean())])
def test_init_clean_never_counts_as_freeze_clean(simple_config, maximum, replies):
    _, result = run_case(replace(simple_config, max_rounds=maximum), StubAgent(replies))
    assert result["status"] == "NON_CONVERGED", result["error"]


@pytest.mark.parametrize(
    "failure,status,count",
    [
        ("not json", "PARSE_ERROR", 1),
        (AgentResult(started=True, error_type="TIMEOUT"), "TIMEOUT", 1),
        (AgentResult(error_type="AGENT_ERROR"), "AGENT_ERROR", 0),
    ],
)
def test_failed_audit_is_not_clean(simple_config, failure, status, count):
    _, result = run_case(simple_config, StubAgent([failure]))
    assert result["status"] == status
    assert result["audit_rounds"] == count
    assert result["review_rounds"] == 0
    if count:
        assert result["trajectory"][0]["status"] == status


@pytest.mark.parametrize("target", ["copied_spec", "source_snapshot", "audit_workspace"])
def test_readonly_evidence_violations_take_priority(simple_config, target):
    def mutate(request):
        run = request.raw_dir.parents[1]
        path = {
            "copied_spec": run / "artifacts/spec.initial.md",
            "source_snapshot": run / "input/task.md",
            "audit_workspace": request.workspace / "unexpected.txt",
        }[target]
        path.write_text("unauthorized mutation")
        return AgentResult(started=True, exit_code=1, error_type="AGENT_ERROR")

    _, result = run_case(simple_config, StubAgent(clean() + [mutate]))
    assert result["status"] == "PROTOCOL_VIOLATION"
    assert result["protocol_violation"]


def test_invalid_review_does_not_create_clean_round(simple_config):
    def missing(request):
        return json.dumps({"audit_id": payload(request)["audit_id"], "decisions": []})

    _, result = run_case(simple_config, StubAgent([audit("P1"), missing]))
    assert result["status"] == "PARSE_ERROR"
    assert result["flow"]["clean"] == []


def test_unchanged_repair_is_rejected(project, simple_config):
    raw = b"# Spec\n\nFailure returns null.\n"
    (project / "cases/sample/task.md").write_bytes(raw)
    change_case(project, lambda d: d["task"].update(sha256=hashlib.sha256(raw).hexdigest()))

    def unchanged(request):
        data = json.loads(repair(request))
        data["spec"] = payload(request)["current_spec"]
        return json.dumps(data)

    _, result = run_case(simple_config, StubAgent([audit("P1"), review(), unchanged]))
    assert result["status"] == "PARSE_ERROR"
    assert "did not change" in result["error"]["message"]
