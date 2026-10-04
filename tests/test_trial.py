import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml
from conftest import StubAgent

from mracbench.cases import case_from_snapshots, load_case, load_trial
from mracbench.cli import main
from mracbench.models import BenchError
from mracbench.repository_mcp import RepositoryReader
from mracbench.spec_checkout import spec_path


@pytest.fixture(autouse=True)
def use_current_protocols(current_project):
    return current_project


class SpecFlowAgent(StubAgent):
    def run(self, request):
        if request.raw_dir.name.startswith("audit-"):
            args = request.mcp_servers["mrac_repository"]["args"]
            reader = RepositoryReader(
                *[
                    Path(args[args.index(flag) + 1])
                    if flag != "--head"
                    else args[args.index(flag) + 1]
                    for flag in ("--repository", "--head", "--manifest", "--audit-log")
                ]
            )
            for tool, values in (("repository_head", {}), ("repository_read", {"path": "app.py"})):
                content = reader.call(tool, values)
                reader.record(tool, values, result=content)
        return super().run(request)


@pytest.mark.parametrize("severity", [None, "P3"])
def test_trial_defaults_to_spec_flow_without_independent_review(
    project, tmp_path, monkeypatch, severity
):
    source = load_case(project, "sample")
    root = tmp_path / "spec-trial"
    shutil.copytree(project / "protocols", root / "protocols")
    spec = tmp_path / "input.md"
    spec.write_text("# Spec\nSpecify the result.\n", encoding="utf-8")

    def audit(request):
        data = json.loads(request.prompt.split("INPUT JSON:\n", 1)[1])
        return json.dumps(
            {
                "audit_id": data["audit_id"],
                "findings": []
                if severity is None or request.raw_dir.name != "audit-01"
                else [
                    {
                        "severity": severity,
                        "title": "Missing result",
                        "evidence": "Spec §1 lacks outcome",
                    }
                ],
            }
        )

    def repair(request):
        target = request.workspace / "docs/spec.md"
        target.write_bytes(target.read_bytes() + b"\nThe missing result is null.\n")
        return "Updated Spec"

    agent = SpecFlowAgent([audit, audit] if severity is None else [audit, repair, audit, audit])
    options = []

    def adapter(executable, **settings):
        options.append(settings)
        return agent

    monkeypatch.setattr("mracbench.cli.CodexExecAdapter", adapter)
    assert (
        main(
            [
                "trial",
                "--name",
                "candidate",
                "--project-root",
                str(root),
                "--spec-file",
                str(spec),
                "--repository-url",
                source.repository_url,
                "--commit",
                source.commit,
                "--spec-path",
                "docs/spec.md",
                "--model",
                "gpt-5.6-luna",
                "--reasoning-effort",
                "high",
                "--windows-sandbox",
                "unelevated",
            ]
        )
        == 0
    )
    path = next((root / "runs").iterdir())
    result = json.loads((path / "result.json").read_bytes())
    assert result["protocol_id"] == "spec-mrac-v2"
    assert result["input_kind"] == "trial" and result["case_version"] is None
    assert "review_rounds" not in result and not (path / "reviews").exists()
    assert result["audit_rounds"] == (2 if severity is None else 3)
    assert result["repair_rounds"] == (0 if severity is None else 1)
    assert options[0]["windows_sandbox"] == "unelevated"
    assert not (root / "cases").exists()


def test_trial_runs_full_simple_flow_without_case_package(project, tmp_path, monkeypatch):
    case = load_case(project, "sample")
    root = tmp_path / "trial-bench"
    shutil.copytree(project / "protocols", root / "protocols")
    spec = tmp_path / "input.md"
    raw = b"\xef\xbb\xbf# Trial Spec\r\n\r\nFailure returns null.\r\n"
    spec.write_bytes(raw)
    agent = StubAgent([json.dumps({"findings": []}), json.dumps({"decisions": []})] * 3)
    monkeypatch.setattr("mracbench.cli.CodexExecAdapter", lambda executable, **options: agent)
    assert (
        main(
            [
                "trial",
                "--name",
                "candidate",
                "--project-root",
                str(root),
                "--spec-file",
                str(spec),
                "--repository-url",
                case.repository_url,
                "--commit",
                case.commit,
                "--model",
                "gpt-6-luna",
                "--protocol",
                "spec-flow-simple-v1",
                "--reasoning-effort",
                "high",
            ]
        )
        == 0
    )
    path = next((root / "runs").iterdir())
    result = json.loads((path / "result.json").read_bytes())
    metadata = yaml.safe_load((path / "run.yaml").read_bytes())
    assert result["input_kind"] == metadata["input_kind"] == "trial"
    assert result["case_version"] is None
    assert result["protocol_id"] == "spec-flow-simple-v1"
    assert result["audit_rounds"] == 3
    assert result["protocol_version"] == 15
    assert result["review_rounds"] == 3
    assert result["frozen_spec_sha256"] == hashlib.sha256(raw).hexdigest()
    assert (path / "artifacts/spec.initial.md").read_bytes() == raw
    assert not (root / "cases").exists()
    assert not (path / "input/case.yaml").exists()
    assert (path / "input/trial.yaml").exists()
    assert all(request.model == "gpt-6-luna" for request in agent.requests)
    assert all(request.reasoning_effort == "high" for request in agent.requests)


def test_trial_snapshots_restore_without_source_files_and_detect_tampering(project, tmp_path):
    source = load_case(project, "sample")
    spec = tmp_path / "spec.md"
    related = tmp_path / "constraints.md"
    spec.write_bytes(b"# Spec\nPin the outcome.\n")
    related.write_bytes(b"# Constraints\nPreserve identity.\n")
    trial = load_trial(
        "candidate", spec, source.repository_url, source.commit, "docs/spec.md", [related]
    )
    snapshots = dict(trial.snapshots)
    restored = case_from_snapshots(trial.id, snapshots)
    assert restored == trial
    assert spec_path(restored) == "docs/spec.md"
    snapshots["task.md"] += b"changed"
    with pytest.raises(BenchError, match="sha256 mismatch"):
        case_from_snapshots(trial.id, snapshots)


def test_trial_repair_and_paused_resume_use_pinned_inputs(project, tmp_path, monkeypatch):
    source = load_case(project, "sample")
    root = tmp_path / "trial-bench"
    shutil.copytree(project / "protocols", root / "protocols")
    spec = tmp_path / "input.md"
    spec.write_bytes(b"# Trial Spec\nSpecify failure behavior.\n")
    original = spec.read_bytes()

    def repair(request):
        target = request.workspace / "docs/spec.md"
        target.write_bytes(target.read_bytes() + b"\nFailure returns null.\n")
        return "Updated Spec"

    finding = json.dumps(
        {
            "findings": [
                {
                    "severity": "P2",
                    "title": "Failure undefined",
                    "evidence": "Missing result clause",
                }
            ]
        }
    )
    accept = json.dumps({"decisions": [{"finding_id": "F1", "outcome": "accepted"}]})
    clean = [json.dumps({"findings": []}), json.dumps({"decisions": []})]
    replies = list(clean)
    for index in range(6):
        replies += [finding, accept]
        if index < 5:
            replies += [repair, "CLOSED"]
    agent = StubAgent(replies)
    monkeypatch.setattr("mracbench.cli.CodexExecAdapter", lambda executable, **options: agent)
    assert (
        main(
            [
                "trial",
                "--name",
                "candidate",
                "--project-root",
                str(root),
                "--spec-file",
                str(spec),
                "--repository-url",
                source.repository_url,
                "--commit",
                source.commit,
                "--spec-path",
                "docs/spec.md",
                "--protocol",
                "spec-flow-simple-v1",
                "--max-rounds",
                "10",
            ]
        )
        == 3
    )
    path = next((root / "runs").iterdir())
    assert spec.read_bytes() == original
    assert (path / "checkout/docs/spec.md").is_file()
    spec.rename(tmp_path / "moved-input.md")
    agent = StubAgent([repair, "CLOSED"] + clean * 2)
    assert main(["resume", "--run-dir", str(path)]) == 0
    result = json.loads((path / "result.json").read_bytes())
    assert result["input_kind"] == "trial"
    assert result["case_version"] is None
    assert result["flow"]["resume_count"] == 1
    assert result["repair_rounds"] == 6
    assert result["audit_rounds"] == 9
    assert (path / "input/task.md").read_bytes() == original
    assert not (root / "cases").exists()


@pytest.mark.parametrize("commit,spec_name", [("main", "SPEC.md"), ("a" * 40, "../spec.md")])
def test_trial_rejects_unpinned_commit_and_unsafe_repair_path(tmp_path, commit, spec_name):
    spec = tmp_path / "spec.md"
    spec.write_text("# Spec\n")
    with pytest.raises(BenchError):
        load_trial("candidate", spec, "https://example.invalid/repo.git", commit, spec_name)
