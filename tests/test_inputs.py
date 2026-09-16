from dataclasses import replace

import pytest
import yaml
from conftest import StubAgent

from mracbench.cases import load_case, load_yaml
from mracbench.models import BenchError
from mracbench.protocol import load_protocol
from mracbench.runner import run_case


def change_case(project, edit):
    path = project / "cases" / "sample" / "case.yaml"
    data = yaml.safe_load(path.read_text())
    edit(data)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_valid_inputs(project):
    case = load_case(project, "sample")
    assert len(case.commit) == 40
    assert case.timeout_seconds == 1800
    assert case.max_audit_rounds is None
    assert load_protocol(project, case.protocol_id).max_audit_rounds == 8


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d.pop("repository"),
        lambda d: d.update(version=True),
        lambda d: d["repository"].update(commit="main"),
        lambda d: d["task"].update(file="../../outside.md"),
        lambda d: d["task"].update(file="missing.md"),
        lambda d: d["track"].update(type="code"),
        lambda d: d.update(limits={"max_audit_rounds": 0}),
        lambda d: d.update(limits={"agent_timeout_seconds": False}),
        lambda d: d["protocol"].update(id="../outside"),
    ],
)
def test_bad_case_is_saved_before_any_agent(project, config, edit):
    change_case(project, edit)
    agent = StubAgent([])
    path, result = run_case(config, agent)
    assert result["status"] == "CASE_ERROR"
    assert result["audit_rounds"] == 0
    assert not agent.requests
    assert (path / "result.json").is_file()


def test_duplicate_yaml_keys_fail():
    with pytest.raises(BenchError):
        load_yaml(b"id: first\nid: second", "case")


def test_invalid_override_is_saved(config):
    _, result = run_case(replace(config, max_rounds=-1), StubAgent([]))
    assert result["status"] == "CASE_ERROR"


def test_missing_protocol_is_saved(project, config):
    (project / "protocols" / "spec-mrac-v1" / "audit.md").unlink()
    _, result = run_case(config, StubAgent([]))
    assert result["status"] == "CASE_ERROR"
