import hashlib
import json
import sys
from dataclasses import replace

import pytest
import yaml
from conftest import StubAgent, git
from test_inputs import change_case

from mracbench.cli import main
from mracbench.codex_exec import CodexExecAdapter
from mracbench.models import AgentResult, BenchError
from mracbench.repository_flow import inspect_repository_run, resume_repository_run, session_lock
from mracbench.runner import run_case


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
                        "title": "Failure outcome",
                        "evidence": "Spec §2 and app.py::value need a defined failure output.",
                    }
                ],
            }
        )

    return reply


def legacy_audit(*, evidence_path="app.py", base=None):
    def reply(request):
        data = json.loads(audit()(request))
        data["repository_review"] = {
            "base_head": base or payload(request)["fixed_repository_head"],
            "checks": [{"path": evidence_path, "symbol": "value", "conclusion": "Compatible."}],
        }
        return json.dumps(data)

    return reply


def repair(request):
    data = payload(request)
    return json.dumps(
        {
            "audit_id": data["audit_id"],
            "disposition": "continue",
            "spec": data["current_spec"]
            + "\nFailure returns null; this revision selects the stated outcome.\n",
            "fixes": [
                {
                    "finding_id": f["finding_id"],
                    "summary": "Defined the selected failure outcome.",
                    "evidence": "Spec intent and app.py permit this in-scope decision; this revision introduces it.",
                }
                for f in data["findings"]
            ],
        }
    )


def needs_input(request):
    return json.dumps(
        {
            "audit_id": payload(request)["audit_id"],
            "disposition": "needs_input",
            "reason": "The external service's failure contract is unavailable.",
            "questions": ["Should the absent external value be returned as null?"],
        }
    )


def clean():
    return [audit()]


def failures(count=6):
    return [call for _ in range(count) for call in (audit("P1"), repair)]


@pytest.fixture
def repo_config(config):
    return replace(config, protocol_id="spec-mrac-v2", reasoning_effort="high")


def test_two_repository_clean_rounds_freeze_original_bytes(project, repo_config):
    raw = b"\xef\xbb\xbf# Spec\r\nFailure returns null.\r\n"
    (project / "cases/sample/task.md").write_bytes(raw)
    change_case(project, lambda d: d["task"].update(sha256=hashlib.sha256(raw).hexdigest()))
    agent = StubAgent(clean() + clean())
    path, result = run_case(repo_config, agent)
    assert result["status"] == "CONVERGED", result["error"]
    assert result["audit_rounds"] == 2 and result["repair_rounds"] == 0
    assert result["flow"]["phase"] == "FROZEN"
    assert (path / "artifacts/spec.initial.md").read_bytes() == raw
    assert result["frozen_spec_sha256"] == hashlib.sha256(raw).hexdigest()
    assert len(set(result["clean_audit_ids"])) == 2
    for request in agent.requests:
        data = payload(request)
        assert "source_spec" in data and "fixed_repository_head" in data
        assert request.reasoning_effort == "high"
        if request.raw_dir.name.startswith("audit"):
            assert "findings" not in data and "user_responses" not in data
    _, checked = inspect_repository_run(path)
    assert checked["status"] == "CONVERGED"
    assert [r.raw_dir.name for r in agent.requests] == ["audit-01", "audit-02"]
    assert result["protocol_version"] == 2 and result["flow"]["schema_version"] == 3
    assert "review_rounds" not in result and "deferred_p3" not in result
    assert not (path / "reviews").exists() and not (path / "input/review.md").exists()
    for saved in (path / "audits").glob("*.json"):
        assert set(json.loads(saved.read_bytes())) == {"audit_id", "findings"}
    for request in agent.requests:
        assert "对 Spec 的正确性和可实施性做审计。" in request.prompt
        assert "repository_review" not in request.prompt and '"checks"' not in request.prompt


@pytest.mark.parametrize("severity", ["P0", "P1", "P2", "P3"])
def test_every_severity_enters_fix_without_review(repo_config, severity):
    agent = StubAgent([audit(severity), repair] + clean() + clean())
    _, result = run_case(repo_config, agent)
    assert result["status"] == "CONVERGED", result["error"]
    assert result["repair_rounds"] == 1
    assert [r.raw_dir.name for r in agent.requests] == [
        "audit-01",
        "repair-01",
        "audit-02",
        "audit-03",
    ]
    assert payload(agent.requests[1])["findings"][0]["severity"] == severity
    assert result["trajectory"][0]["reported_by_severity"][severity] == 1
    assert "accepted_count" not in result["trajectory"][0]


@pytest.mark.parametrize(
    "path,base", [("missing.py", None), ("../app.py", None), ("app.py", "a" * 40), (".", None)]
)
def test_invalid_repository_evidence_cannot_be_clean(repo_config, path, base):
    _, result = run_case(repo_config, StubAgent([legacy_audit(evidence_path=path, base=base)]))
    assert result["status"] == "AUDIT_INVALID"
    assert result["flow"]["active_audit"] is not None
    assert result["flow"]["clean"] == []


def test_optional_legacy_checklist_is_preserved_but_not_required(repo_config):
    path, result = run_case(repo_config, StubAgent([legacy_audit(), audit()]))
    assert result["status"] == "CONVERGED", result["error"]
    first = json.loads((path / "audits/audit-01.json").read_bytes())
    assert first["repository_review"]["checks"][0]["path"] == "app.py"
    assert "repository_review" not in json.loads((path / "audits/audit-02.json").read_bytes())

    def bad(request):
        data = json.loads(legacy_audit()(request))
        data["repository_review"]["checks"] = []
        return json.dumps(data)

    _, invalid = run_case(repo_config, StubAgent([bad]))
    assert invalid["status"] == "AUDIT_INVALID"


def test_malformed_json_is_not_rewritten_and_resume_abandons_audit(repo_config):
    def bad(request):
        return audit()(request) + "}"

    path, result = run_case(repo_config, StubAgent([bad]))
    assert result["status"] == "AUDIT_INVALID"
    raw = (path / "raw/audit-01/final.txt").read_bytes()
    assert "Extra data" in result["error"]["message"]
    _, resumed = resume_repository_run(path, StubAgent(clean() + clean()))
    assert resumed["status"] == "CONVERGED", resumed["error"]
    assert resumed["audit_rounds"] == 3
    assert resumed["trajectory"][0]["status"] == "abandoned"
    assert resumed["clean_audit_ids"][0].endswith("audit-02")
    assert (path / "raw/audit-01/final.txt").read_bytes() == raw


def test_six_round_pause_is_after_repair_and_resume_is_fresh(project, repo_config):
    change_case(project, lambda d: d.update(limits={"max_audit_rounds": 1}))
    path, result = run_case(repo_config, StubAgent(failures()))
    assert result["status"] == "PAUSED", result["error"]
    assert (result["audit_rounds"], result["repair_rounds"]) == (6, 6)
    assert result["flow"]["pending_fix"] is None
    assert result["flow"]["phase"] == "PAUSED"
    assert result["final_artifact"].endswith("spec.round-06.md")
    metadata = yaml.safe_load((path / "run.yaml").read_text())
    assert metadata["effective_config"]["max_audit_rounds"] is None
    # Changes to live packages/defaults must not change the resumed run.
    (project / "protocols/spec-mrac-v2/audit.md").write_text("Do not use this live revision")
    agent = StubAgent(clean() + clean())
    _, result = resume_repository_run(path, agent)
    assert result["status"] == "CONVERGED", result["error"]
    assert result["audit_rounds"] == 8
    assert "live revision" not in agent.requests[0].prompt


def test_p3_only_resets_failure_streak_but_still_requires_repair(repo_config):
    replies = failures(5) + [audit("P3"), repair] + failures(1) + clean() + clean()
    _, result = run_case(repo_config, StubAgent(replies))
    assert result["status"] == "CONVERGED", result["error"]
    assert result["flow"]["resume_count"] == 0
    assert result["audit_rounds"] == 9
    assert result["repair_rounds"] == 7


def test_p3_interrupts_clean_and_cannot_freeze_without_repair(repo_config):
    agent = StubAgent(clean() + [audit("P3"), needs_input])
    path, result = run_case(repo_config, agent)
    assert result["status"] == "NEEDS_INPUT"
    assert result["flow"]["clean"] == []
    assert result["flow"]["pending_fix"]["findings"][0]["severity"] == "P3"
    assert result["trajectory"][-1]["status"] == "issues_found"
    assert "P3x1" in (path / "run-report.md").read_text(encoding="utf-8")


def test_missing_input_preserves_fix_until_response(repo_config, tmp_path):
    path, result = run_case(repo_config, StubAgent([audit("P1"), needs_input]))
    assert result["status"] == "NEEDS_INPUT", result["error"]
    assert result["flow"]["phase"] == "FIX"
    pending = result["flow"]["pending_fix"]
    empty = StubAgent([])
    _, waiting = resume_repository_run(path, empty)
    assert waiting["flow"]["pending_fix"] == pending and not empty.requests
    response = tmp_path / "response.md"
    response.write_text("Return null; this behavior is authorized.", encoding="utf-8")
    agent = StubAgent([repair] + clean() + clean())
    _, final = resume_repository_run(path, agent, input_file=response)
    assert final["status"] == "CONVERGED", final["error"]
    assert agent.requests[0].raw_dir.name == "repair-02"
    assert "authorized" in payload(agent.requests[0])["user_responses"][0]["content"]
    assert payload(agent.requests[0])["findings"] == pending["findings"]


def test_timeout_audit_is_abandoned_on_resume(repo_config):
    path, result = run_case(
        repo_config, StubAgent([AgentResult(started=True, error_type="TIMEOUT")])
    )
    assert result["status"] == "TIMEOUT"
    _, result = resume_repository_run(path, StubAgent(clean() + clean()))
    assert result["status"] == "CONVERGED", result["error"]
    assert result["trajectory"][0]["status"] == "abandoned"
    assert result["audit_rounds"] == 3


def test_unstarted_repair_retries_with_new_request_name(repo_config):
    path, result = run_case(
        repo_config, StubAgent([audit("P1"), AgentResult(error_type="AGENT_ERROR")])
    )
    assert result["flow"]["phase"] == "FIX"
    agent = StubAgent([repair] + clean() + clean())
    _, result = resume_repository_run(path, agent)
    assert result["status"] == "CONVERGED", result["error"]
    assert agent.requests[0].raw_dir.name == "repair-02"
    assert result["repair_rounds"] == 1


def test_explicit_cap_finishes_last_repair(repo_config):
    _, result = run_case(replace(repo_config, max_rounds=1), StubAgent(failures(1)))
    assert result["status"] == "NON_CONVERGED", result["error"]
    assert result["repair_rounds"] == 1 and result["flow"]["pending_fix"] is None


def test_working_spec_change_invalidates_active_audit_and_imports_revision(repo_config):
    def mutate(request):
        (request.raw_dir.parents[1] / "working/spec.md").write_text(
            "# Updated\nNew current text.\n"
        )
        return audit()(request)

    path, result = run_case(repo_config, StubAgent([mutate]))
    assert result["status"] == "AUDIT_STALE"
    with pytest.raises(BenchError, match="Working Spec changed"):
        inspect_repository_run(path)
    agent = StubAgent(clean() + clean())
    _, result = resume_repository_run(path, agent)
    assert result["status"] == "CONVERGED", result["error"]
    assert result["trajectory"][0]["status"] == "abandoned"
    assert "New current text" in payload(agent.requests[0])["current_spec"]
    assert (path / "artifacts/spec.initial.md").read_text() != (
        path / result["final_artifact"]
    ).read_text()


def test_frozen_change_and_old_state_versions_cannot_reuse_clean(repo_config):
    path, result = run_case(repo_config, StubAgent(clean() + clean()))
    assert result["status"] == "CONVERGED"
    (path / "working/spec.md").write_text("Changed frozen content")
    with pytest.raises(BenchError) as exc:
        inspect_repository_run(path)
    assert exc.value.kind == "FROZEN_SPEC_CHANGED"
    checkpoint_path = path / "repository-state.json"
    checkpoint = json.loads(checkpoint_path.read_bytes())
    checkpoint["schema_version"] = 1
    checkpoint_path.write_text(json.dumps(checkpoint))
    with pytest.raises(BenchError, match="Unsupported state"):
        resume_repository_run(path, StubAgent([]))


def test_abort_retains_pending_findings(repo_config):
    path, result = run_case(repo_config, StubAgent([audit("P1"), needs_input]))
    _, aborted = inspect_repository_run(path, abort_reason="Stop this experiment")
    assert aborted["status"] == "ABORTED"
    assert aborted["flow"]["pending_fix"] == result["flow"]["pending_fix"]
    with pytest.raises(BenchError, match="Cannot resume ABORTED"):
        resume_repository_run(path, StubAgent([]))


def test_run_lock_rejects_concurrent_resume(repo_config):
    path, result = run_case(repo_config, StubAgent([audit("P1"), needs_input]))
    with session_lock(path), pytest.raises(BenchError, match="still active"):
        resume_repository_run(path, StubAgent([]))
    assert result["status"] == "NEEDS_INPUT"


def test_fixed_repository_instructions_are_discoverable(project, repo_config):
    case = yaml.safe_load((project / "cases/sample/case.yaml").read_text())
    upstream = project.parent / "upstream"
    (upstream / "AGENTS.md").write_text("Use the app.py contract when reviewing this repository.\n")
    git(upstream, "add", "AGENTS.md")
    git(upstream, "commit", "-qm", "instructions")
    case["repository"]["commit"] = git(upstream, "rev-parse", "HEAD")
    (project / "cases/sample/case.yaml").write_text(yaml.safe_dump(case))
    agent = StubAgent(clean() + clean())
    _, result = run_case(repo_config, agent)
    assert result["status"] == "CONVERGED", result["error"]
    assert "AGENTS.md" in payload(agent.requests[0])["repository_instruction_paths"]


def test_reused_actual_auditor_id_cannot_freeze(repo_config):
    def repeated(request):
        return AgentResult(
            started=True,
            exit_code=0,
            final_text=audit()(request),
            metadata={"thread_id": "same-auditor"},
        )

    _, result = run_case(repo_config, StubAgent([repeated, repeated]))
    assert result["status"] == "AUDIT_INVALID"
    assert "identity reused" in result["error"]["message"]
    assert len(result["flow"]["clean"]) == 1


def test_interrupted_started_audit_is_counted_once_and_live_child_prevents_resume(
    repo_config, monkeypatch
):
    def interrupted(request):
        (request.raw_dir / "invocation.json").write_text(
            json.dumps(
                {
                    "started": True,
                    "ended_at": None,
                    "pid": 123456,
                }
            )
        )
        raise RuntimeError("simulated executor interruption before returning AgentResult")

    path, result = run_case(repo_config, StubAgent([interrupted]))
    assert result["status"] == "INTERNAL_ERROR"
    assert result["audit_rounds"] == 0
    monkeypatch.setattr("mracbench.repository_flow.pid_alive", lambda pid: True)
    with pytest.raises(BenchError, match="still alive"):
        resume_repository_run(path, StubAgent([]))
    monkeypatch.setattr("mracbench.repository_flow.pid_alive", lambda pid: False)
    _, result = resume_repository_run(path, StubAgent(clean() + clean()))
    assert result["status"] == "CONVERGED", result["error"]
    assert result["audit_rounds"] == 3
    assert result["trajectory"][0]["status"] == "abandoned"


def test_cap_on_sixth_round_finishes_repair_without_soft_pause(repo_config):
    path, result = run_case(replace(repo_config, max_rounds=6), StubAgent(failures()))
    assert result["status"] == "NON_CONVERGED", result["error"]
    assert result["repair_rounds"] == 6
    assert result["flow"]["pending_fix"] is None
    assert not (path / "pauses").exists()


def test_saved_evidence_cannot_be_repaired_by_importing_working_spec(repo_config, tmp_path):
    path, result = run_case(repo_config, StubAgent([audit("P1"), needs_input]))
    assert result["status"] == "NEEDS_INPUT"
    (path / "artifacts/spec.initial.md").write_text("corrupted old evidence")
    revision = tmp_path / "revision.md"
    revision.write_text("# New current Spec\nIntact replacement\n")
    with pytest.raises(BenchError, match="Immutable evidence changed"):
        resume_repository_run(path, StubAgent([]), spec_file=revision)


def test_repository_flow_through_real_subprocess_adapter(repo_config, tmp_path):
    script = tmp_path / "fake_codex.py"
    script.write_text(
        """import json, sys, uuid
from pathlib import Path
if "--version" in sys.argv:
    print("fake-codex 1")
    raise SystemExit(0)
prompt = sys.stdin.buffer.read().decode("utf-8")
data = json.loads(prompt.split("INPUT JSON:\\n", 1)[1])
result = {"audit_id": data["audit_id"], "findings": []}
Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text(json.dumps(result), encoding="utf-8")
print(json.dumps({"type": "thread.started", "thread_id": str(uuid.uuid4())}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}))
""",
        encoding="utf-8",
    )
    agent = CodexExecAdapter(command=[sys.executable, str(script)])
    path, result = run_case(repo_config, agent)
    assert result["status"] == "CONVERGED", result["error"]
    ids = result["flow"]["auditor_ids"]
    assert len(set(ids)) == 2 and all(not item.startswith("fresh-exec:") for item in ids)
    execution = json.loads((path / "raw/audit-01/execution.json").read_bytes())
    assert execution["metadata"]["thread_id"] == ids[0]


def test_all_mixed_findings_remain_pending_when_repair_omits_one(repo_config):
    def mixed(request):
        data = json.loads(audit("P1")(request))
        data["findings"] += json.loads(audit("P3")(request))["findings"]
        return json.dumps(data)

    def incomplete(request):
        data = json.loads(repair(request))
        data["fixes"].pop()
        return json.dumps(data)

    path, result = run_case(repo_config, StubAgent([mixed, incomplete]))
    assert result["status"] == "FIX_INVALID"
    pending = result["flow"]["pending_fix"]["findings"]
    assert [f["severity"] for f in pending] == ["P1", "P3"]
    assert result["flow"]["clean"] == []
    assert result["final_artifact"] == "artifacts/spec.initial.md"
    agent = StubAgent([repair] + clean() + clean())
    _, resumed = resume_repository_run(path, agent)
    assert resumed["status"] == "CONVERGED", resumed["error"]
    assert payload(agent.requests[0])["findings"] == pending


def test_clean_round_resets_six_round_failure_counter(repo_config):
    _, result = run_case(
        repo_config, StubAgent(failures(5) + clean() + failures(1) + clean() + clean())
    )
    assert result["status"] == "CONVERGED", result["error"]
    assert result["audit_rounds"] == 9 and result["repair_rounds"] == 6
    assert result["clean_audit_ids"][0].endswith("audit-08")


@pytest.mark.parametrize("kind", ["clean", "fix", "invalid-review"])
def test_historical_runs_are_read_only_without_rewriting_evidence(repo_config, capsys, kind):
    # Build a self-contained historical snapshot, including its original pinned revision.
    replies = clean() + clean() if kind == "clean" else [audit("P1"), needs_input]
    path, _ = run_case(repo_config, StubAgent(replies))
    checkpoint_path = path / "repository-state.json"
    checkpoint = json.loads(checkpoint_path.read_bytes())
    result = checkpoint["result"]
    checkpoint["schema_version"] = result["flow"]["schema_version"] = 2
    result["protocol_version"] = 1
    result["review_rounds"] = result["audit_rounds"]
    result["deferred_p3"] = []
    for row in result["trajectory"]:
        row["accepted_by_severity"] = row.pop("reported_by_severity")
        row["accepted_count"] = row["reported_count"]
    pending = result["flow"]["pending_fix"]
    if pending:
        pending["accepted"] = pending.pop("findings")
    if kind == "clean":
        # Old clean could include a deferred P3; it must never become a new clean.
        result["trajectory"][0]["reported_count"] = 1
        result["deferred_p3"] = [
            {
                "audit_id": result["trajectory"][0]["audit_id"],
                "title": "Legacy optional wording",
                "reason": "Deferred by old parent",
            }
        ]
    elif kind == "invalid-review":
        result["status"] = "REVIEW_INVALID"
        result["flow"].update(phase="AUDIT", pending_fix=None, active_audit=result["trajectory"][0])
        result["error"] = {"type": "REVIEW_INVALID", "message": "Insufficient coverage"}

    metadata = yaml.safe_load((path / "run.yaml").read_bytes())
    metadata["protocol_version"] = 1
    protocol = yaml.safe_load((path / "input/protocol.yaml").read_bytes())
    protocol["version"] = 1
    protocol["source"]["state_version"] = 2
    protocol["stages"]["review"] = {"prompt": "review.md"}
    (path / "input/protocol.yaml").write_text(yaml.safe_dump(protocol), encoding="utf-8")
    (path / "input/review.md").write_text("Legacy adjudication prompt", encoding="utf-8")
    pinned = json.loads((path / "input/execution-config.json").read_bytes())
    pinned["protocol_version"] = 1
    (path / "input/execution-config.json").write_text(json.dumps(pinned), encoding="utf-8")
    for relative in ("input/protocol.yaml", "input/review.md", "input/execution-config.json"):
        sha = hashlib.sha256((path / relative).read_bytes()).hexdigest()
        checkpoint["evidence_sha256"][relative] = sha
        metadata["input_sha256"][relative[6:]] = sha
    metadata["evidence_sha256"] = checkpoint["evidence_sha256"]
    (path / "run.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    (path / "state.json").write_text(json.dumps(result), encoding="utf-8")
    (path / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (path / "run-report.md").write_text(
        "Historical report: preserve exact bytes.", encoding="utf-8"
    )

    def snapshot():
        return {
            p.relative_to(path): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in path.rglob("*")
            if p.is_file()
        }

    # Read-only history must not recreate a missing process-lock file either.
    (path / ".repository-run.lock").unlink()
    before = snapshot()
    _, inspected = inspect_repository_run(path)
    assert inspected["status"] == result["status"]
    assert inspected["flow"] == result["flow"]
    for command in ("status", "report"):
        main([command, "--run-dir", str(path)])
    assert "Historical adjudicated run (read-only)" in capsys.readouterr().out
    agent = StubAgent([])
    with pytest.raises(BenchError, match="read-only"):
        resume_repository_run(path, agent)
    with pytest.raises(BenchError, match="read-only"):
        inspect_repository_run(path, abort_reason="Must not change historical evidence")
    assert not agent.requests
    assert snapshot() == before


def test_old_protocol_revision_cannot_start_with_new_engine(project, repo_config):
    protocol_path = project / "protocols/spec-mrac-v2/protocol.yaml"
    protocol = yaml.safe_load(protocol_path.read_bytes())
    protocol["version"] = 1
    protocol["stages"]["review"] = {"prompt": "review.md"}
    protocol_path.write_text(yaml.safe_dump(protocol), encoding="utf-8")
    (protocol_path.parent / "review.md").write_text("Old adjudication", encoding="utf-8")
    agent = StubAgent([])
    _, result = run_case(repo_config, agent)
    assert result["status"] == "CASE_ERROR"
    assert "Unsupported protocol revision" in result["error"]["message"]
    assert not agent.requests
