import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import StubAgent, git
from test_inputs import change_case

from mracbench.codex_exec import CodexExecAdapter
from mracbench.exec_flow import ExecEngine, inspect_exec, resume_exec
from mracbench.models import AgentResult, BenchError
from mracbench.protocol import render_exec_prompt
from mracbench.runner import run_case


def payload(request):
    return json.loads(request.prompt.split("INPUT JSON:\n", 1)[1])


def outcome(findings=None):
    result = {
        "disposition": "complete",
        "summary": "Implemented the required value.",
        "validation": [
            {
                "command": "python -m pytest",
                "status": "not_run",
                "evidence": "Fixture exercises controller behavior, not product tests.",
            }
        ],
    }
    if findings is not None:
        result["fixes"] = [
            {
                "finding_id": f["finding_id"],
                "summary": "Corrected product behavior.",
                "evidence": "The required branch now exists in app.py.",
            }
            for f in findings
        ]
    return json.dumps(result)


def implement(request):
    (request.workspace / "app.py").write_text("value = 2\n", encoding="utf-8")
    return outcome()


def repair(request):
    path = request.workspace / "app.py"
    path.write_text(path.read_text() + f"fixed_{path.stat().st_size} = True\n", encoding="utf-8")
    return outcome(payload(request)["current_findings"])


def audit(severity=None):
    def reply(request):
        data = payload(request)
        return json.dumps(
            {
                "audit_id": data["audit_id"],
                "spec_sha256": data["spec_sha256"],
                "candidate_sha256": data["candidate_sha256"],
                "findings": []
                if severity is None
                else [
                    {
                        "severity": severity,
                        "title": "Missing branch",
                        "evidence": "Spec §2 requires the missing app.py branch.",
                    }
                ],
            }
        )

    return reply


def first_batch(last_clean=False):
    replies = [implement]
    for index in range(6):
        replies.append(audit(None if last_clean and index == 5 else "P1"))
        if index < 5:
            replies.append(repair)
    return replies


@pytest.fixture
def exec_config(config, tmp_path):
    spec = tmp_path / "chosen-spec.md"
    spec.write_bytes(b"\xef\xbb\xbf# Execution Spec\r\n\r\nSet the value to 2.\r\n")
    return replace(config, protocol_id="exec-mrac-v1", spec_file=spec, reasoning_effort="high")


def test_implements_then_two_independent_zero_issue_audits(exec_config):
    source_bytes = exec_config.spec_file.read_bytes()
    agent = StubAgent([implement, audit(), audit()])
    path, result = run_case(exec_config, agent)
    assert result["status"] == "CONVERGED", result["error"]
    assert (result["implement_rounds"], result["audit_rounds"], result["repair_rounds"]) == (
        1,
        2,
        0,
    )
    assert [request.readonly for request in agent.requests] == [False, True, True]
    assert all(request.reasoning_effort == "high" for request in agent.requests)
    assert (path / "input/execution-spec.md").read_bytes() == source_bytes
    assert exec_config.spec_file.read_bytes() == source_bytes
    assert "Correct the behavior of app.py." not in agent.requests[0].prompt
    assert len(set(result["clean_audit_ids"])) == 2
    assert (
        result["trajectory"][0]["candidate_sha256"] == result["trajectory"][1]["candidate_sha256"]
    )
    assert result["flow"]["audit_limit"] == 6
    assert b"+value = 2" in (path / result["final_artifact"]).read_bytes()
    checkout = Path(result["final_checkout"])
    assert git(checkout, "diff", "--cached", "--name-only") == ""
    assert (exec_config.project_root.parent / "upstream/app.py").read_text() == "value = 1\n"
    assert inspect_exec(path)[1]["status"] == "CONVERGED"


def test_patch_includes_untracked_binary_and_deleted_files(exec_config, tmp_path):
    def change(request):
        (request.workspace / "app.py").unlink()
        (request.workspace / "new code.py").write_text("value = 2\n", encoding="utf-8")
        (request.workspace / "binary.bin").write_bytes(bytes(range(256)))
        (request.workspace / "ignored.txt").write_text("test output")
        return outcome()

    agent = StubAgent([change, audit(), audit()])
    path, result = run_case(exec_config, agent)
    assert result["status"] == "CONVERGED", result["error"]
    data = payload(agent.requests[1])
    manifest = json.loads((path / result["flow"]["candidate"]["manifest"]).read_bytes())
    assert "product_changes" not in data and "ignored_files" not in data
    assert "candidate_manifest_path" not in data
    assert set(manifest) == {"base_head", "tree", "signature", "workspace_sha256", "patch_sha256"}
    assert len(json.dumps(manifest)) < 1000
    assert (
        git(
            agent.requests[1].workspace,
            "ls-files",
            "--others",
            "--ignored",
            "--exclude-standard",
            "--",
            "ignored.txt",
        )
        == "ignored.txt"
    )
    checkout = agent.requests[1].workspace
    assert git(checkout, "diff", "--name-only", data["base_head"], "--") == "app.py"
    assert set(git(checkout, "ls-files", "--others", "--exclude-standard").splitlines()) == {
        "binary.bin",
        "new code.py",
    }
    target = tmp_path / "apply-target"
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--no-hardlinks",
            str(exec_config.project_root.parent / "upstream"),
            str(target),
        ],
        check=True,
    )
    git(target, "apply", "--binary", "--index", str(path / result["final_artifact"]))
    assert not (target / "app.py").exists()
    assert (target / "new code.py").read_text() == "value = 2\n"
    assert (target / "binary.bin").read_bytes() == bytes(range(256))
    assert not (target / "ignored.txt").exists()


@pytest.mark.parametrize("readonly", [False, True])
def test_exec_prompt_does_not_grow_with_generated_file_inventory(tmp_path, readonly):
    engine = object.__new__(ExecEngine)
    engine.spec = "# Fixed execution Spec"
    engine.case = SimpleNamespace(commit="a" * 40)
    engine.repo = SimpleNamespace(path=tmp_path / "checkout", last_snapshot={})
    engine.store = SimpleNamespace(evidence=SimpleNamespace(path=lambda name: tmp_path / name))
    engine.result = {"spec_sha256": "b" * 64}
    engine.flow = {
        "candidate": {
            "signature": "c" * 64,
            "tree": "d" * 40,
            "patch": "candidates/0002/changes.patch",
            "patch_sha256": "e" * 64,
            "manifest": "candidates/0002/manifest.json",
        },
        "validation": [],
    }
    before = render_exec_prompt("Inspect the candidate.", engine.inputs(), readonly=readonly)
    engine.repo.last_snapshot = {
        "ignored_files": {
            f"Merge-Blast/Library/PackageCache/generated-{number:05d}/artifact.bin": "f" * 64
            for number in range(30000)
        },
        "changes": [{"status": "A", "path": f"new-{number}.py"} for number in range(30000)],
    }
    after = render_exec_prompt("Inspect the candidate.", engine.inputs(), readonly=readonly)
    assert after == before
    assert len(after) < 10000
    assert "git ls-files --others --exclude-standard" in after
    assert "git diff --no-ext-diff --no-textconv <base_head> --" in after


def test_sixth_issue_pauses_before_repair_and_resume_adds_six(exec_config, project):
    change_case(project, lambda d: d.update(limits={"max_audit_rounds": 1}))
    path, result = run_case(exec_config, StubAgent(first_batch()))
    assert result["status"] == "PAUSED", result["error"]
    assert (result["audit_rounds"], result["repair_rounds"]) == (6, 5)
    assert result["flow"]["phase"] == "FIX" and result["flow"]["pending_fix"]
    previous = result["flow"]["candidate"]["signature"]
    originals = {p: p.read_bytes() for p in (path / "audits").iterdir()}
    exec_config.spec_file.write_text(
        "Changed live file; the execution snapshot remains authoritative."
    )
    agent = StubAgent([repair, audit(), audit()])
    _, final = resume_exec(path, agent)
    assert final["status"] == "CONVERGED", final["error"]
    assert final["flow"]["audit_limit"] == 12
    assert len(final["flow"]["budget_extensions"]) == 1
    assert final["audit_rounds"] == 8 and final["repair_rounds"] == 6
    assert agent.requests[0].raw_dir.name == "repair-06"
    assert "Changed live file" not in payload(agent.requests[0])["execution_spec"]
    assert final["final_candidate_sha256"] != previous
    assert all(p.read_bytes() == raw for p, raw in originals.items())


def test_sixth_first_clean_is_preserved_across_extension(exec_config):
    path, result = run_case(exec_config, StubAgent(first_batch(last_clean=True)))
    assert result["status"] == "PAUSED", result["error"]
    assert result["flow"]["pending_fix"] is None
    assert len(result["flow"]["clean"]) == 1
    _, final = resume_exec(path, StubAgent([audit()]))
    assert final["status"] == "CONVERGED", final["error"]
    assert final["convergence_round"] == 7
    assert final["repair_rounds"] == 5


def test_accepted_p3_also_requires_code_repair(exec_config):
    agent = StubAgent([implement, audit("P3"), repair, audit(), audit()])
    _, result = run_case(exec_config, agent)
    assert result["status"] == "CONVERGED", result["error"]
    assert result["repair_rounds"] == 1
    assert result["trajectory"][0]["issue_count"] == 1
    assert all("current_findings" not in payload(r) for r in agent.requests if r.readonly)


@pytest.mark.parametrize("target", ["product", "ignored", "ignored_existing", "spec"])
def test_auditor_writes_are_protocol_violations(exec_config, target):
    def seed(request):
        if target == "ignored_existing":
            (request.workspace / "ignored.txt").write_text("original output")
        return implement(request)

    def mutate(request):
        file = {
            "product": request.workspace / "app.py",
            "ignored": request.workspace / "ignored.txt",
            "ignored_existing": request.workspace / "ignored.txt",
            "spec": request.raw_dir.parents[1] / "input/execution-spec.md",
        }[target]
        file.write_text("auditor mutation")
        return audit()(request)

    _, result = run_case(exec_config, StubAgent([seed, mutate]))
    assert result["status"] == "PROTOCOL_VIOLATION", result["error"]
    assert result["protocol_violation"] and not result["clean_audit_ids"]


def test_writable_stage_cannot_change_git_control(exec_config):
    def mutate(request):
        git(request.workspace, "config", "mrac.unexpected", "true")
        return outcome()

    _, result = run_case(exec_config, StubAgent([mutate]))
    assert result["status"] == "PROTOCOL_VIOLATION", result["error"]


def test_external_candidate_change_refuses_resume_without_extending_budget(exec_config):
    path, result = run_case(exec_config, StubAgent(first_batch(last_clean=True)))
    before = (path / "exec-state.json").read_bytes()
    (Path(result["final_checkout"]) / "app.py").write_text("external new code")
    with pytest.raises(BenchError, match="Saved code candidate changed"):
        resume_exec(path, StubAgent([]))
    assert (path / "exec-state.json").read_bytes() == before


def test_invalid_audit_cannot_count_clean_and_error_recovery_does_not_add_budget(exec_config):
    path, result = run_case(exec_config, StubAgent([implement, "not JSON"]))
    assert result["status"] == "AUDIT_INVALID"
    assert result["audit_rounds"] == 1
    _, final = resume_exec(path, StubAgent([audit(), audit()]))
    assert final["status"] == "CONVERGED", final["error"]
    assert final["audit_rounds"] == 3 and final["flow"]["audit_limit"] == 6
    assert final["trajectory"][0]["status"] == "abandoned"


def test_partial_implementation_can_resume_from_saved_error(exec_config):
    def partial(request):
        (request.workspace / "app.py").write_text("value = 0\n")
        return AgentResult(started=True, error_type="TIMEOUT")

    path, result = run_case(exec_config, StubAgent([partial]))
    assert result["status"] == "TIMEOUT"
    assert b"+value = 0" in (path / result["final_artifact"]).read_bytes()
    agent = StubAgent([implement, audit(), audit()])
    _, final = resume_exec(path, agent)
    assert final["status"] == "CONVERGED", final["error"]
    assert final["implement_rounds"] == 2 and final["flow"]["audit_limit"] == 6


def test_each_run_gets_separate_checkout(exec_config):
    first, a = run_case(exec_config, StubAgent([implement, audit(), audit()]))
    second, b = run_case(exec_config, StubAgent([implement, audit(), audit()]))
    assert a["status"] == b["status"] == "CONVERGED"
    assert first != second and a["final_checkout"] != b["final_checkout"]


def test_explicit_spec_and_fixed_batch_size_are_required(exec_config):
    for config in (replace(exec_config, spec_file=None), replace(exec_config, max_rounds=8)):
        agent = StubAgent([])
        _, result = run_case(config, agent)
        assert result["status"] == "CASE_ERROR"
        assert not agent.requests


def test_missing_input_preserves_current_fix(exec_config, tmp_path):
    def need(request):
        data = json.loads(outcome())
        data.update(
            disposition="needs_input", questions=["Which fixture contains the service contract?"]
        )
        return json.dumps(data)

    path, result = run_case(exec_config, StubAgent([implement, audit("P1"), need]))
    assert result["status"] == "NEEDS_INPUT", result["error"]
    pending = result["flow"]["pending_fix"]
    assert pending
    assert resume_exec(path, StubAgent([]))[1]["flow"]["pending_fix"] == pending
    response = tmp_path / "answer.md"
    response.write_text("The fixture is app.py; retain the Spec behavior.")
    agent = StubAgent([repair, audit(), audit()])
    _, final = resume_exec(path, agent, response)
    assert final["status"] == "CONVERGED", final["error"]
    assert payload(agent.requests[0])["user_responses"]


def test_repair_must_change_product_code_not_only_ignored_output(exec_config):
    def no_product_fix(request):
        (request.workspace / "ignored.txt").write_text("New test output only")
        return outcome(payload(request)["current_findings"])

    _, result = run_case(exec_config, StubAgent([implement, audit("P2"), no_product_fix]))
    assert result["status"] == "FIX_INVALID"
    assert result["flow"]["pending_fix"]


def test_repeated_auditor_session_cannot_converge(exec_config):
    def same(request):
        return AgentResult(
            started=True,
            exit_code=0,
            final_text=audit()(request),
            metadata={"thread_id": "reused-auditor"},
        )

    _, result = run_case(exec_config, StubAgent([implement, same, same]))
    assert result["status"] == "AUDIT_INVALID"
    assert not result["clean_audit_ids"]


def test_failed_sixth_audit_requires_explicit_extension(exec_config):
    replies = first_batch()
    replies[-1] = AgentResult(started=True, error_type="TIMEOUT")
    path, result = run_case(exec_config, StubAgent(replies))
    assert result["status"] == "TIMEOUT"
    assert result["audit_rounds"] == 6 and result["flow"]["audit_limit"] == 6
    _, final = resume_exec(path, StubAgent([audit(), audit()]))
    assert final["status"] == "CONVERGED", final["error"]
    assert final["flow"]["audit_limit"] == 12
    assert final["trajectory"][5]["status"] == "abandoned"


def test_multiple_budget_extensions_are_six_each(exec_config):
    path, result = run_case(exec_config, StubAgent(first_batch()))
    assert result["status"] == "PAUSED"
    second = [repair]
    for index in range(6):
        second.append(audit("P2"))
        if index < 5:
            second.append(repair)
    _, result = resume_exec(path, StubAgent(second))
    assert result["status"] == "PAUSED", result["error"]
    assert (result["audit_rounds"], result["repair_rounds"]) == (12, 11)
    _, result = resume_exec(path, StubAgent([repair, audit(), audit()]))
    assert result["status"] == "CONVERGED", result["error"]
    assert [(e["from"], e["to"]) for e in result["flow"]["budget_extensions"]] == [
        (6, 12),
        (12, 18),
    ]
    assert result["audit_rounds"] == 14


def test_real_subprocess_adapter_uses_write_and_read_sandboxes(exec_config, tmp_path):
    script = tmp_path / "executor.py"
    script.write_text(
        """import json,sys,uuid
from pathlib import Path
if "--version" in sys.argv:
    print("fake-executor 1")
    raise SystemExit(0)
data=json.loads(sys.stdin.buffer.read().decode("utf-8").split("INPUT JSON:\\n",1)[1])
mode=sys.argv[sys.argv.index("--sandbox")+1]
if "audit_id" in data:
    assert mode=="read-only"
    result={k:data[k] for k in ("audit_id","spec_sha256","candidate_sha256")}
    result["findings"]=[]
else:
    assert mode=="workspace-write"
    Path("app.py").write_text("value = 2\\n")
    result={"disposition":"complete","summary":"Changed value","validation":[{"command":"fixture check","status":"not_run","evidence":"controller fixture"}]}
Path(sys.argv[sys.argv.index("--output-last-message")+1]).write_text(json.dumps(result),encoding="utf-8")
print(json.dumps({"type":"thread.started","thread_id":str(uuid.uuid4())}))
""",
        encoding="utf-8",
    )
    _, result = run_case(exec_config, CodexExecAdapter(command=[sys.executable, str(script)]))
    assert result["status"] == "CONVERGED", result["error"]
    assert len(result["flow"]["auditor_ids"]) == 2
