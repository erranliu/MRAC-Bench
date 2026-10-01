import hashlib
import json
import os
import subprocess
import sys

from mracbench.repository_mcp import RepositoryReader


def snapshot(tmp_path, name, text):
    root = tmp_path / "repo"
    root.mkdir()
    data = text.encode("utf-8")
    (root / name).write_bytes(data)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({name: hashlib.sha256(data).hexdigest()}), encoding="utf-8")
    return root, manifest


def test_repository_mcp_utf8_wire_with_inherited_gbk_stdio(tmp_path):
    name = "源码.cs"
    text = "// 中文注释 ✅\nclass Example {}\n"
    root, manifest = snapshot(tmp_path, name, text)
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "repository_read", "arguments": {"path": name}},
    }
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "mracbench.repository_mcp",
            "--repository",
            str(root),
            "--head",
            "0" * 40,
            "--manifest",
            str(manifest),
            "--audit-log",
            str(tmp_path / "events.jsonl"),
        ],
        input=(json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"),
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "gbk"},
        timeout=10,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    frame = json.loads(process.stdout.decode("utf-8"))
    result = json.loads(frame["result"]["content"][0]["text"])
    assert result["path"] == name
    assert result["lines"][0]["text"] == "// 中文注释 ✅"


def test_requested_counts_are_bounded_without_an_argument_retry(tmp_path):
    root, manifest = snapshot(tmp_path, "Example.cs", "match\n" * 300)
    reader = RepositoryReader(root, "0" * 40, manifest, tmp_path / "events.jsonl")
    read = reader.read({"path": "Example.cs", "max_lines": 260})
    assert len(read["lines"]) == 200
    assert read["truncated"]
    search = reader.search({"query": "match", "max_results": 80})
    assert len(search["matches"]) == 50
    assert search["truncated"]
