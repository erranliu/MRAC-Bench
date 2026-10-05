import hashlib
import json
import subprocess
import sys

import pytest

from mracbench.spec_file_mcp import SpecFile, server_config


def workspace(tmp_path, seed=b"# Spec\r\n\r\nChinese: \xe4\xb8\xad\xe6\x96\x87\r\n"):
    root = tmp_path / "checkout"
    target = root / "docs/spec.md"
    target.parent.mkdir(parents=True)
    target.write_bytes(seed)
    return root, target, SpecFile(root, "docs/spec.md", tmp_path / "events.jsonl")


def test_utf8_edit_preserves_bom_crlf_and_rejects_stale_hash(tmp_path):
    root, target, tool = workspace(tmp_path, b"\xef\xbb\xbf# Spec\r\n\r\n" + "中文\r\n".encode())
    original = target.read_bytes()
    status = tool.call("spec_read", {})
    result = tool.call(
        "spec_edit",
        {"expected_sha256": status["sha256"], "old_text": "中文\n", "new_text": "中文修订\n"},
    )
    assert target.read_bytes() == b"\xef\xbb\xbf# Spec\r\n\r\n" + "中文修订\r\n".encode()
    assert result["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="changed"):
        tool.call(
            "spec_edit",
            {"expected_sha256": status["sha256"], "old_text": "Spec", "new_text": "New"},
        )
    assert original != target.read_bytes()
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()) == [
        "docs/spec.md"
    ]


@pytest.mark.parametrize(
    "name", ["../escape", ".git/config", "docs/.GIT/config", "C:/escape", "docs/spec.md:stream"]
)
def test_spec_path_rejects_escape_and_git_metadata(tmp_path, name):
    with pytest.raises(ValueError):
        SpecFile(tmp_path, name, tmp_path.parent / "events.jsonl")


def test_ambiguous_edit_and_unknown_file_argument_do_not_change_bytes(tmp_path):
    _, target, tool = workspace(tmp_path, b"same same")
    args = {
        "expected_sha256": hashlib.sha256(b"same same").hexdigest(),
        "old_text": "same",
        "new_text": "different",
    }
    with pytest.raises(ValueError, match="exactly once"):
        tool.call("spec_edit", args)
    with pytest.raises(ValueError, match="Unexpected"):
        tool.call("spec_edit", {**args, "path": "other.md"})
    assert target.read_bytes() == b"same same"


def test_native_mcp_read_edit_exchange_records_verified_change(tmp_path):
    root, target, _ = workspace(tmp_path)
    log = tmp_path / "events.jsonl"
    sha = hashlib.sha256(target.read_bytes()).hexdigest()
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "spec_read", "arguments": {}},
        },
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "spec_edit",
                "arguments": {"expected_sha256": sha, "old_text": "中文", "new_text": "修订中文"},
            },
        },
    ]
    args = server_config(root, "docs/spec.md", log)["mrac_spec_file"]["args"]
    process = subprocess.run(
        [sys.executable, *args],
        input="".join(json.dumps(m) + "\n" for m in messages),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    replies = [json.loads(line) for line in process.stdout.splitlines()]
    assert not replies[-1]["result"].get("isError")
    assert "修订中文" in target.read_text(encoding="utf-8")
    assert len(log.read_text(encoding="utf-8").splitlines()) == 2
