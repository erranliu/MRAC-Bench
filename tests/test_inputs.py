import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from conftest import CLEAN, SPEC, StubAgent

from mracbench.cases import load_case, load_yaml
from mracbench.models import BenchError
from mracbench.protocol import DEFAULT_PROTOCOL_ID, load_protocol
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
    assert not hasattr(case, "protocol_id")
    assert load_protocol(project, DEFAULT_PROTOCOL_ID).max_audit_rounds is None


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


def test_related_spec_pinned_bytes_and_snapshot(project):
    raw = b"\xef\xbb\xbf# Related Spec\r\n\r\nAn existing ownership boundary.\r\n"
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
    case = load_case(project, "sample")
    assert case.snapshots["related-spec-01.md"] == raw
    assert case.related_specs[0]["content"] == raw.decode("utf-8-sig")


@pytest.mark.parametrize(
    "entry",
    [
        {"file": "related.md", "sha256": "0" * 64},
        {"file": "../related.md", "sha256": "0" * 64},
        {"file": "related.md"},
        {"file": "task.md", "sha256": "0" * 64},
    ],
)
def test_bad_related_spec_fails_before_agent(project, config, entry):
    (project / "cases/sample/related.md").write_text("# Related\nBody\n")
    change_case(project, lambda d: d.update(related_specs=[entry]))
    agent = StubAgent([])
    _, result = run_case(config, agent)
    assert result["status"] == "CASE_ERROR"
    assert agent.requests == []


def test_unknown_workflow_is_rejected(project, config):
    protocol = project / "protocols/spec-mrac-v1/protocol.yaml"
    data = yaml.safe_load(protocol.read_text())
    data["workflow"] = "arbitrary-workflow"
    protocol.write_text(yaml.safe_dump(data))
    _, result = run_case(config, StubAgent([]))
    assert result["status"] == "CASE_ERROR"


@pytest.mark.parametrize("protocol_id", ["does-not-exist", "../outside", ""])
def test_explicit_protocol_is_validated_without_fallback(config, protocol_id):
    _, result = run_case(replace(config, protocol_id=protocol_id), StubAgent([]))
    assert result["status"] == "CASE_ERROR"


@pytest.mark.parametrize("legacy", [{"id": "spec-flow-simple-v1"}, "invalid-legacy-value"])
@pytest.mark.parametrize("selected", [None, "spec-mrac-v1"])
def test_legacy_case_protocol_never_selects_or_blocks_a_run(project, config, legacy, selected):
    from test_repository_flow import clean as repository_clean

    change_case(project, lambda d: d.update(protocol=legacy))
    original = (project / "cases/sample/case.yaml").read_bytes()
    replies = [SPEC, CLEAN, CLEAN] if selected else repository_clean() + repository_clean()
    path, result = run_case(replace(config, protocol_id=selected), StubAgent(replies))
    assert result["status"] == "CONVERGED", result["error"]
    assert result["protocol_id"] == (selected or DEFAULT_PROTOCOL_ID)
    metadata = yaml.safe_load((path / "run.yaml").read_text())
    assert metadata["protocol_selection"] == ("explicit" if selected else "default")
    assert "case_default_protocol" not in metadata
    assert (path / "input/case.yaml").read_bytes() == original
    assert (project / "cases/sample/case.yaml").read_bytes() == original
