import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from mracbench.models import AgentResult, RunConfig

PROJECT = Path(__file__).resolve().parents[1]
CLEAN = json.dumps({"status": "clean", "issues": []})
ISSUE = {
    "id": "A1",
    "severity": "blocking",
    "title": "Missing behavior",
    "description": "Behavior is underspecified",
    "evidence": "Source line 1",
    "required_change": "Define the intended behavior",
}
BLOCKING = json.dumps({"status": "issues_found", "issues": [ISSUE]})
SPEC = "# Implementation spec\n\nDefine the requested behavior and acceptance checks.\n"


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


@pytest.fixture
def project(tmp_path):
    source = tmp_path / "upstream"
    source.mkdir()
    git(source, "init", "--quiet")
    git(source, "config", "user.email", "tests@example.invalid")
    git(source, "config", "user.name", "MRAC tests")
    (source / "app.py").write_text("value = 1\n", encoding="utf-8")
    (source / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    git(source, "add", ".")
    git(source, "commit", "--quiet", "-m", "fixture")
    root = tmp_path / "bench"
    case_dir = root / "cases" / "sample"
    case_dir.mkdir(parents=True)
    shutil.copytree(PROJECT / "protocols", root / "protocols")
    task_raw = b"Correct the behavior of app.py.\n"
    data = {
        "id": "sample",
        "version": 1,
        "repository": {"url": source.as_uri(), "commit": git(source, "rev-parse", "HEAD")},
        "task": {"file": "task.md", "sha256": hashlib.sha256(task_raw).hexdigest()},
        "track": {"type": "spec"},
        "metadata": {"secret_prompt_marker": "METADATA_MUST_NOT_LEAK"},
    }
    (case_dir / "case.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    (case_dir / "task.md").write_bytes(task_raw)
    return root


@pytest.fixture
def config(project):
    return RunConfig(
        project,
        "sample",
        project / "runs",
        project / ".workspaces",
        model="test-model",
        protocol_id="spec-mrac-v1",
    )


class StubAgent:
    agent_type = "stub"

    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    def version(self):
        return "test"

    def run(self, request):
        self.requests.append(request)
        if request.raw_dir.name.startswith("repository-read-check-"):
            server = request.mcp_servers["mrac_repository"]
            args = server["args"]
            root = Path(args[args.index("--repository") + 1])
            head = args[args.index("--head") + 1]
            source_path = next(root.rglob("*.py"), None) or next(root.rglob("*.md"), None)
            assert source_path is not None
            relative = source_path.relative_to(root).as_posix()
            excerpt = source_path.read_text(encoding="utf-8").splitlines()[0]
            match = {"path": relative, "line": 1, "text": excerpt}
            log = Path(args[args.index("--audit-log") + 1])
            log.write_text(
                "\n".join(
                    json.dumps(event)
                    for event in [
                        {
                            "tool": "repository_head",
                            "arguments": {},
                            "ok": True,
                            "result": {"head": head},
                        },
                        {
                            "tool": "repository_search",
                            "arguments": {
                                "query": None,
                                "globs": ["*.py", "*.md"],
                                "max_results": 5,
                            },
                            "ok": True,
                            "result": {
                                "query": None,
                                "matches": [match],
                                "scanned_files": 1,
                                "truncated": False,
                            },
                        },
                        {
                            "tool": "repository_read",
                            "arguments": {"path": relative, "start_line": 1, "max_lines": 1},
                            "ok": True,
                            "result": {
                                "path": relative,
                                "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
                                "start_line": 1,
                                "lines": [{"line": 1, "text": excerpt}],
                                "truncated": True,
                            },
                        },
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            return AgentResult(
                final_text=json.dumps(
                    {"head": head, "path": relative, "match": excerpt, "excerpt": excerpt}
                ),
                stdout="repository MCP probe",
                exit_code=0,
                started=True,
            )
        reply = next(self.replies)
        if callable(reply):
            reply = reply(request)
        if isinstance(reply, AgentResult):
            return reply
        return AgentResult(
            final_text=reply, stdout="raw stream", stderr="diagnostic", exit_code=0, started=True
        )
