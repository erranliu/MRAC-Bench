import json
import subprocess
import sys

import pytest

from mracbench.candidate_mcp import CandidateWorkspace
from mracbench.models import BenchError
from mracbench.simple_repair import prepare_workspace, verify_workspace


def workspace(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    files = {
        "spec.md": b"# Example\r\n\r\nFailure behavior is unclear.\r\n",
        "source-spec.md": b"# Example\nFailure must return null.\n",
        "accepted-findings.json": b'[{"finding_id":"F1"}]\n',
    }
    prepare_workspace(raw, files)
    return raw, files


def test_candidate_tools_edit_only_the_spec_and_preserve_line_endings(tmp_path):
    raw, files = workspace(tmp_path)
    reader = CandidateWorkspace(
        raw / "workspace", raw / "workspace-before.json", raw / "events.jsonl", writable=True
    )
    assert reader.call("candidate_search", {"query": "Failure"})["matches"][0]["line"] == 3
    assert reader.call("candidate_read", {"path": "source-spec.md"})["lines"][1]["text"] == (
        "Failure must return null."
    )
    result = reader.call(
        "candidate_edit",
        {"old_text": "Failure behavior is unclear.", "new_text": "Failure returns null."},
    )
    assert result["before_sha256"] != result["after_sha256"]
    assert (raw / "workspace/spec.md").read_bytes() == b"# Example\r\n\r\nFailure returns null.\r\n"
    assert reader.call("candidate_status", {})["changed"]
    assert verify_workspace(raw / "workspace", files, writable={"spec.md"})["spec.md"]
    with pytest.raises(ValueError, match="exactly once"):
        reader.call("candidate_edit", {"old_text": "missing", "new_text": "new"})
    (raw / "workspace/source-spec.md").write_text("changed")
    with pytest.raises(ValueError, match="Immutable candidate input changed"):
        reader.call("candidate_status", {})
    with pytest.raises(BenchError, match="Immutable repair input changed"):
        verify_workspace(raw / "workspace", files, writable={"spec.md"})


def test_candidate_mcp_stdio_exposes_edit_only_in_writable_mode(tmp_path):
    raw, _ = workspace(tmp_path)
    read_only = CandidateWorkspace(
        raw / "workspace", raw / "workspace-before.json", raw / "readonly.jsonl", writable=False
    )
    with pytest.raises(ValueError, match="unavailable"):
        read_only.call(
            "candidate_edit", {"old_text": "Failure behavior is unclear.", "new_text": "fixed"}
        )
    command = [
        sys.executable,
        "-m",
        "mracbench.candidate_mcp",
        "--workspace",
        str(raw / "workspace"),
        "--manifest",
        str(raw / "workspace-before.json"),
        "--audit-log",
        str(raw / "events.jsonl"),
        "--writable",
    ]
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "candidate_edit",
                "arguments": {
                    "old_text": "Failure behavior is unclear.",
                    "new_text": "Failure returns null.",
                },
            },
        },
    ]
    process = subprocess.run(
        command,
        input="\n".join(json.dumps(item) for item in requests) + "\n",
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    replies = [json.loads(line) for line in process.stdout.splitlines()]
    assert {item["name"] for item in replies[1]["result"]["tools"]} >= {"candidate_edit"}
    assert "Failure returns null." in (raw / "workspace/spec.md").read_text()
    assert json.loads((raw / "events.jsonl").read_text())["ok"]
