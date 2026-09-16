import json

import pytest
from conftest import CLEAN, SPEC, StubAgent
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
    agent = StubAgent([SPEC, CLEAN, CLEAN])
    monkeypatch.setattr("mracbench.cli.CodexExecAdapter", lambda executable: agent)
    assert main(["run", "--case", "sample", "--project-root", str(project)]) == 0
    assert "CONVERGED" in capsys.readouterr().out
    results = list((project / "runs").glob("*/result.json"))
    assert json.loads(results[0].read_text())["protocol_id"] == "spec-mrac-v1"


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
