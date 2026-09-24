import json
import sys
import time
from dataclasses import replace

from mracbench.codex_exec import CodexExecAdapter
from mracbench.models import AgentRequest


def fake_command(tmp_path, body):
    script = tmp_path / "fake_codex.py"
    script.write_text("import sys, pathlib, time, json\n" + body, encoding="utf-8")
    return [sys.executable, str(script)]


def request(tmp_path, timeout=5):
    return AgentRequest(
        'Task with Unicode: 中文 and literals $() ` "',
        tmp_path,
        tmp_path / "raw",
        timeout,
        "test-model",
    )


def test_final_message_is_separate_from_event_stream(tmp_path):
    command = fake_command(
        tmp_path,
        """
prompt = sys.stdin.buffer.read().decode('utf-8')
assert '中文' in prompt
path = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])
path.write_text('# Spec\\n\\nBody.\\n', encoding='utf-8')
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':5}}))
print(json.dumps({'type':'thread.started','thread_id':'fresh-audit-session'}))
print('diagnostic', file=sys.stderr)
""",
    )
    response = CodexExecAdapter(command=command).run(request(tmp_path))
    assert response.success
    assert response.final_text == "# Spec\n\nBody.\n"
    assert "turn.completed" in response.stdout
    assert "diagnostic" in response.stderr
    assert response.usage["input_tokens"] == 10
    assert response.metadata["thread_id"] == "fresh-audit-session"
    args = response.metadata["command"]
    assert "resume" not in args
    assert "--ephemeral" in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert args[-1] == "-"


def test_spec_only_workspace_flags_and_explicit_effort(tmp_path):
    command = fake_command(tmp_path, "print('ok')\n")
    response = CodexExecAdapter(command=command).run(
        replace(
            request(tmp_path),
            reasoning_effort="high",
            skip_git_repo_check=True,
        )
    )
    assert response.success
    args = response.metadata["command"]
    assert 'model_reasoning_effort="high"' in args
    assert "--skip-git-repo-check" in args
    assert response.metadata["reasoning_effort"] == "high"


def test_output_schema_is_passed_as_a_saved_file(tmp_path):
    command = fake_command(tmp_path, "print('ok')\n")
    schema = {
        "type": "object",
        "properties": {"spec": {"type": "string"}},
        "required": ["spec"],
        "additionalProperties": False,
    }
    response = CodexExecAdapter(command=command).run(
        replace(request(tmp_path), output_schema=schema)
    )
    assert response.success
    args = response.metadata["command"]
    schema_path = tmp_path / "raw" / "output-schema.json"
    assert args[args.index("--output-schema") + 1] == str(schema_path)
    assert json.loads(schema_path.read_text(encoding="utf-8")) == schema


def test_timeout_preserves_partial_output_and_terminates(tmp_path):
    command = fake_command(tmp_path, "print('partial', flush=True)\ntime.sleep(30)\n")
    result = CodexExecAdapter(command=command).run(request(tmp_path, 1))
    assert result.error_type == "TIMEOUT"
    assert result.started
    assert "partial" in result.stdout
    assert result.duration_seconds < 15
    assert json.loads((tmp_path / "raw" / "invocation.json").read_text())["error_type"] == "TIMEOUT"


def test_missing_executable_returns_unstarted_error(tmp_path):
    result = CodexExecAdapter("nonexistent-mrac-codex-xyz").run(request(tmp_path))
    assert not result.started
    assert result.error_type == "AGENT_ERROR"
    assert (tmp_path / "raw" / "stderr.txt").exists()


def test_nonzero_exit_is_not_a_success(tmp_path):
    command = fake_command(tmp_path, "print('failure', file=sys.stderr)\nsys.exit(7)\n")
    result = CodexExecAdapter(command=command).run(request(tmp_path))
    assert result.error_type == "AGENT_ERROR"
    assert result.exit_code == 7


def test_timeout_stops_descendant_process(tmp_path):
    child = tmp_path / "child.py"
    heartbeat = tmp_path / "heartbeat.txt"
    child.write_text(
        "import sys,time\nfrom pathlib import Path\n"
        "while True:\n"
        " with Path(sys.argv[1]).open('a') as f: f.write('x')\n"
        " time.sleep(0.05)\n",
        encoding="utf-8",
    )
    command = fake_command(
        tmp_path,
        "import subprocess\n"
        f"subprocess.Popen([sys.executable, {str(child)!r}, {str(heartbeat)!r}])\n"
        "time.sleep(30)\n",
    )
    result = CodexExecAdapter(command=command).run(request(tmp_path, 1))
    assert result.error_type == "TIMEOUT"
    size = heartbeat.stat().st_size
    assert size > 0
    time.sleep(0.3)
    assert heartbeat.stat().st_size == size
