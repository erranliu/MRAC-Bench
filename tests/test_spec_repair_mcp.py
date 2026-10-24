import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from conftest import git
from test_inputs import change_case
from test_spec_budget import audit
from test_trial import SpecFlowAgent

from mracbench.repository_flow import resume_repository_run
from mracbench.repository_mcp import RepositoryReader
from mracbench.runner import run_case
from mracbench.spec_file_mcp import SpecFile


@pytest.fixture(autouse=True)
def use_current_protocols(current_project):
    return current_project


def repository_reader(request):
    args = request.mcp_servers["mrac_repository"]["args"]
    return RepositoryReader(
        Path(args[args.index("--repository") + 1]),
        args[args.index("--head") + 1],
        Path(args[args.index("--manifest") + 1]),
        Path(args[args.index("--audit-log") + 1]),
    )


def edit(request):
    data = json.loads(request.prompt.split("INPUT JSON:\n", 1)[1])
    target = request.workspace / data["spec_path"]
    target.write_bytes(target.read_bytes() + b"\nFailure returns null.\n")
    return "Updated the Spec."


@pytest.mark.parametrize("repairs", [1, 2])
def test_repair_mcp_reads_pinned_source_and_edits_current_spec(project, config, repairs):
    upstream = project.parent / "upstream"
    (upstream / "AGENTS.md").write_text("# Fixed repository rules\n", encoding="utf-8")
    (upstream / "docs").mkdir()
    (upstream / "docs/spec.md").write_text("# Baseline Spec\n", encoding="utf-8")
    git(upstream, "add", ".")
    git(upstream, "commit", "-qm", "Add operational rules and baseline Spec")
    head = git(upstream, "rev-parse", "HEAD")
    change_case(project, lambda data: data["repository"].update(commit=head))
    change_case(project, lambda data: data["track"].update(path="docs/spec.md"))
    seen_specs = []

    def repair(request):
        assert set(request.mcp_servers) == {"mrac_repository", "mrac_spec_file"}
        reader = repository_reader(request)
        assert reader.root != request.workspace
        assert reader.call("repository_head", {}) == {"head": head}
        assert reader.call("repository_read", {"path": "AGENTS.md"})["lines"]
        assert reader.call("repository_read", {"path": "app.py"})["lines"][0]["text"] == "value = 1"
        assert reader.call("repository_search", {"query": "value", "globs": ["*.py"]})["matches"]
        baseline = reader.call("repository_read", {"path": "docs/spec.md"})
        assert baseline["lines"][0]["text"] == "# Baseline Spec"
        with pytest.raises(ValueError):
            reader.call("repository_read", {"path": "../outside.md"})
        tool = SpecFile(
            request.workspace, "docs/spec.md", request.raw_dir / "spec-file-events.jsonl"
        )
        current = tool.call("spec_read", {})
        text = "\n".join(line["text"] for line in current["lines"])
        assert "# Baseline Spec" not in text
        seen_specs.append(text)
        result = tool.call(
            "spec_edit",
            {
                "expected_sha256": current["sha256"],
                "old_text": text,
                "new_text": text + "\nFailure returns null.\n",
            },
        )
        tool.record("spec_edit", {}, result=result)
        assert "prefer these supplied MCP tools over native shell and apply_patch" in request.prompt
        assert "baseline version, not the current repair candidate" in request.prompt
        assert "Edit only the named Spec" in request.prompt
        return "Saved through the Spec MCP tool."

    replies = [call for _ in range(repairs) for call in (lambda r: audit(r, "P2"), repair)]
    agent = SpecFlowAgent(replies + [audit, audit])
    path, result = run_case(replace(config, protocol_id="spec-mrac-v2"), agent)
    assert result["status"] == "CONVERGED", result["error"]
    assert result["protocol_version"] == 6 and result["repair_rounds"] == repairs
    assert len(seen_specs) == repairs
    if repairs == 2:
        assert seen_specs[1].count("Failure returns null.") == 1
    assert (upstream / "docs/spec.md").read_text() == "# Baseline Spec\n"
    for request in agent.requests:
        if request.raw_dir.name.startswith("repair-"):
            assert not request.readonly
            saved = json.loads((request.raw_dir / "mcp-servers.json").read_bytes())
            assert set(saved) == {"mrac_repository", "mrac_spec_file"}
    assert len(list((path / "repairs").glob("*.diff"))) == repairs


def test_saved_v5_repair_keeps_original_tools_after_default_upgrade(project, config):
    descriptor = project / "protocols/spec-mrac-v2/protocol.yaml"
    data = yaml.safe_load(descriptor.read_bytes())
    data["version"] = 5
    descriptor.write_text(yaml.safe_dump(data), encoding="utf-8")
    needs = json.dumps(
        {
            "disposition": "needs_input",
            "reason": "Missing external fact",
            "questions": ["Which outcome?"],
        }
    )
    agent = SpecFlowAgent([lambda r: audit(r, "P2"), needs])
    path, result = run_case(replace(config, protocol_id="spec-mrac-v2"), agent)
    assert result["status"] == "NEEDS_INPUT"
    data["version"] = 6
    descriptor.write_text(yaml.safe_dump(data), encoding="utf-8")
    answer = project / "answer.md"
    answer.write_text("Failure returns null.", encoding="utf-8")
    resumed_agent = SpecFlowAgent([edit, audit, audit])
    _, resumed = resume_repository_run(path, resumed_agent, answer)
    assert resumed["status"] == "CONVERGED", resumed["error"]
    assert resumed["protocol_version"] == 5
    for request in [*agent.requests, *resumed_agent.requests]:
        if request.raw_dir.name.startswith("repair-"):
            assert set(request.mcp_servers) == {"mrac_spec_file"}
            assert "prefer these supplied MCP tools" not in request.prompt
            assert "Use ordinary file tools" in request.prompt


@pytest.mark.parametrize("prefix", ["", "Audit complete.\n"])
def test_v6_still_rejects_json_wrapped_in_markdown(config, prefix):
    def fenced(request):
        return prefix + "```json\n" + audit(request) + "\n```"

    agent = SpecFlowAgent([fenced])
    _, result = run_case(replace(config, protocol_id="spec-mrac-v2"), agent)
    assert result["status"] == "AUDIT_INVALID"
    assert result["audit_rounds"] == 1 and result["repair_rounds"] == 0
    assert len(agent.requests) == 1
