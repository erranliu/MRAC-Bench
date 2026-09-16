import json

import pytest

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
