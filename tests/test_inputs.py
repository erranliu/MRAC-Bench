import hashlib
from dataclasses import replace
from pathlib import Path

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
        lambda d: d["task"].pop("sha256"),
        lambda d: d["task"].update(sha256=None),
        lambda d: d["task"].update(sha256=True),
        lambda d: d["task"].update(sha256=123),
        lambda d: d["task"].update(sha256="a" * 63),
        lambda d: d["task"].update(sha256="z" * 64),
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


@pytest.mark.parametrize("change", ["content", "line_endings", "bom"])
def test_changed_spec_bytes_fail_before_any_agent(project, config, change):
    path = project / "cases" / "sample" / "task.md"
    raw = path.read_bytes()
    modified = {
        "content": raw + b"Additional context must not be absorbed.\n",
        "line_endings": raw.replace(b"\n", b"\r\n"),
        "bom": b"\xef\xbb\xbf" + raw,
    }[change]
    path.write_bytes(modified)
    agent = StubAgent([])
    _, result = run_case(config, agent)
    assert result["status"] == "CASE_ERROR"
    assert "task.sha256 mismatch" in result["error"]["message"]
    assert not agent.requests


def test_spec_filename_and_exact_bytes_are_supported(project):
    raw = "\ufeff# 固定 Spec\r\n\r\n只使用本文件中的需求。\r\n".encode("utf-8")
    path = project / "cases" / "sample" / "spec.md"
    path.write_bytes(raw)
    change_case(
        project,
        lambda d: d["task"].update(file="spec.md", sha256=hashlib.sha256(raw).hexdigest().upper()),
    )
    case = load_case(project, "sample")
    assert case.snapshots["task.md"] == raw
    assert case.task == raw.decode("utf-8-sig")


def test_bundled_case_has_a_valid_pinned_task():
    case = load_case(Path(__file__).resolve().parents[1], "psf__requests-1963")
    assert case.task
