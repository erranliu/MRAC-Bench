import json

import pytest
from conftest import StubAgent
from test_simple_flow import audit, clean, review

from mracbench.cli import main


def test_invalid_case_has_reviewable_cli_error(tmp_path, capsys):
    exit_code = main(["run", "--case", "missing", "--project-root", str(tmp_path)])
    assert exit_code == 2
    output = capsys.readouterr().out
    assert "CASE_ERROR" in output
    results = list((tmp_path / "runs").glob("*/result.json"))
    assert len(results) == 1
    assert str(results[0]) in output
    assert json.loads(results[0].read_text())["audit_rounds"] == 0


def test_unwritable_output_root_reports_cli_failure(tmp_path, capsys):
    file = tmp_path / "not-a-directory"
    file.write_text("keep me")
    with pytest.raises(SystemExit) as exc:
        main(["run", "--case", "missing", "--runs-dir", str(file)])
    assert exc.value.code == 2
    assert "Cannot persist run" in capsys.readouterr().err
    assert file.read_text() == "keep me"


def test_cli_protocol_override_and_reasoning_are_recorded(project, monkeypatch, capsys):
    agent = StubAgent(clean() + clean() + clean())
    monkeypatch.setattr("mracbench.cli.CodexExecAdapter", lambda executable: agent)
    case_path = project / "cases/sample/case.yaml"
    case_before = case_path.read_bytes()
    assert (
        main(
            [
                "run",
                "--case",
                "sample",
                "--project-root",
                str(project),
                "--protocol",
                "spec-flow-simple-v1",
                "--model",
                "test-model",
                "--reasoning-effort",
                "high",
            ]
        )
        == 0
    )
    assert "CONVERGED" in capsys.readouterr().out
    assert all(request.reasoning_effort == "high" for request in agent.requests)
    assert case_path.read_bytes() == case_before


def test_cli_uses_run_default_for_case_without_protocol(project, monkeypatch, capsys):
    from test_repository_flow import clean as repository_clean

    agent = StubAgent(repository_clean() + repository_clean())
    monkeypatch.setattr("mracbench.cli.CodexExecAdapter", lambda executable: agent)
    assert main(["run", "--case", "sample", "--project-root", str(project)]) == 0
    assert "CONVERGED" in capsys.readouterr().out
    results = list((project / "runs").glob("*/result.json"))
    assert json.loads(results[0].read_text())["protocol_id"] == "spec-mrac-v2"


def test_cli_blocked_exit_code(project, monkeypatch, capsys):
    agent = StubAgent([audit("P1"), review(exception="product-decision")])
    monkeypatch.setattr("mracbench.cli.CodexExecAdapter", lambda executable: agent)
    assert (
        main(
            [
                "run",
                "--case",
                "sample",
                "--project-root",
                str(project),
                "--protocol",
                "spec-flow-simple-v1",
            ]
        )
        == 4
    )
    assert "BLOCKED" in capsys.readouterr().out


def test_cli_resume_rejects_missing_run_without_mutation(tmp_path, capsys):
    path = tmp_path / "missing"
    assert main(["resume", "--run-dir", str(path)]) == 2
    assert "RESUME_ERROR" in capsys.readouterr().out
    assert not path.exists()


def test_cli_v2_reports_questions_resumes_fix_and_verifies_frozen_report(
    project, tmp_path, monkeypatch, capsys
):
    from test_repository_flow import audit as repo_audit
    from test_repository_flow import clean as repo_clean
    from test_repository_flow import needs_input, repair

    agent = StubAgent([repo_audit("P1"), needs_input])
    monkeypatch.setattr("mracbench.cli.CodexExecAdapter", lambda executable: agent)
    assert (
        main(["run", "--case", "sample", "--project-root", str(project), "--model", "test-model"])
        == 5
    )
    assert "Required input:" in capsys.readouterr().out
    path = next((project / "runs").iterdir())
    answer = tmp_path / "answer.md"
    answer.write_text("Null is the selected behavior.")
    agent = StubAgent([repair] + repo_clean() + repo_clean())
    monkeypatch.setattr("mracbench.cli.CodexExecAdapter", lambda executable: agent)
    assert main(["resume", "--run-dir", str(path), "--input-file", str(answer)]) == 0
    assert main(["report", "--run-dir", str(path)]) == 0
    assert "Baseline:" in capsys.readouterr().out


def test_cli_exec_requires_spec_and_uses_explicit_execution_protocol(
    project, tmp_path, monkeypatch, capsys
):
    from test_exec_flow import audit, implement

    spec = tmp_path / "approved.md"
    spec.write_text("# Approved Spec\nSet value to 2.\n")
    agent = StubAgent([implement, audit(), audit()])
    monkeypatch.setattr("mracbench.cli.CodexExecAdapter", lambda executable: agent)
    assert (
        main(
            [
                "run",
                "--case",
                "sample",
                "--project-root",
                str(project),
                "--protocol",
                "exec-mrac-v1",
                "--spec-file",
                str(spec),
            ]
        )
        == 0
    )
    assert "CONVERGED" in capsys.readouterr().out
    path = next((project / "runs").iterdir())
    assert main(["report", "--run-dir", str(path)]) == 0
    assert "Candidate patch:" in capsys.readouterr().out
    before = (path / "exec-state.json").read_bytes()
    assert main(["resume", "--run-dir", str(path), "--spec-file", str(spec)]) == 2
    assert (path / "exec-state.json").read_bytes() == before
