import json
import os
import sys
import time
from dataclasses import replace

import pytest

from mracbench.codex_exec import CodexExecAdapter, saved_windows_sandbox
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
    assert "features.apps=false" in args
    assert response.metadata["apps_enabled"] is False
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


@pytest.mark.skipif(os.name != "nt", reason="Windows sandbox selection")
@pytest.mark.parametrize("mode", ["elevated", "unelevated"])
def test_windows_sandbox_selection_is_explicit_and_recorded(tmp_path, mode):
    command = fake_command(tmp_path, "print('ok')\n")
    result = CodexExecAdapter(command=command, windows_sandbox=mode).run(request(tmp_path))
    assert result.success
    assert f'windows.sandbox="{mode}"' in result.metadata["command"]
    assert result.metadata["windows_sandbox"] == mode


def test_sandbox_provisioning_failure_is_detected_despite_zero_exit(tmp_path):
    event = {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "status": "failed",
            "exit_code": -1,
            "command": "git rev-parse HEAD",
            "aggregated_output": "Failed to create unified exec process: sandbox provisioning failed",
        },
    }
    command = fake_command(tmp_path, f"print(json.dumps({event!r}))\n")
    result = CodexExecAdapter(command=command).run(request(tmp_path))
    assert result.exit_code == 0
    assert not result.success
    assert result.error_type == "EXECUTION_ENVIRONMENT_ERROR"
    persisted = json.loads((tmp_path / "raw/invocation.json").read_text())
    assert persisted["error_type"] == result.error_type


def test_agent_quoting_sandbox_failure_does_not_trigger_environment_error(tmp_path):
    event = {
        "type": "item.completed",
        "item": {
            "type": "agent_message",
            "text": "Failed to create unified exec process: sandbox provisioning failed",
        },
    }
    command = fake_command(tmp_path, f"print(json.dumps({event!r}))\n")
    assert CodexExecAdapter(command=command).run(request(tmp_path)).success


def test_resume_restores_saved_windows_sandbox(tmp_path):
    assert saved_windows_sandbox(tmp_path) == "elevated"
    (tmp_path / "run.yaml").write_text(
        "effective_config:\n  windows_sandbox: unelevated\n", encoding="utf-8"
    )
    assert saved_windows_sandbox(tmp_path) == "unelevated"


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


def completed_but_hanging(tmp_path, *, pending=False, final="done", message="done"):
    return fake_command(
        tmp_path,
        f"""
sys.stdin.buffer.read()
path = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])
path.write_text({final!r}, encoding='utf-8')
if {pending!r}:
 print(json.dumps({{'type':'item.started','item':{{'id':'edit','type':'file_change','status':'in_progress'}}}}),flush=True)
print(json.dumps({{'type':'item.completed','item':{{'type':'agent_message','text':{message!r}}}}}),flush=True)
print(json.dumps({{'type':'turn.completed','usage':{{'input_tokens':10}}}}),flush=True)
time.sleep(30)
""",
    )


def test_completed_turn_is_verified_and_hanging_process_is_cleaned_early(tmp_path):
    result = CodexExecAdapter(
        command=completed_but_hanging(tmp_path), completion_grace_seconds=0.15
    ).run(request(tmp_path))
    assert result.success, result.error_message
    assert result.exit_code != 0  # Preserve the actual killed-process exit code.
    assert result.metadata["validated_completed_turn"] is True
    assert result.metadata["forced_cleanup"] is True
    assert result.metadata["cleanup_reason"] == "process_remained_after_completed_turn"
    assert result.duration_seconds < 3
    assert result.metadata["progress"]["post_completion_seconds"] < 2
    assert result.usage == {"input_tokens": 10}


@pytest.mark.parametrize("pending,final", [(True, "done"), (False, ""), (False, "different")])
def test_completed_event_cannot_hide_unfinished_tools_or_missing_or_wrong_final(
    tmp_path, pending, final
):
    result = CodexExecAdapter(
        command=completed_but_hanging(tmp_path, pending=pending, final=final),
        completion_grace_seconds=0.15,
    ).run(request(tmp_path))
    assert not result.success
    assert result.error_type == "EXECUTION_ENVIRONMENT_ERROR"
    assert result.metadata["validated_completed_turn"] is False
    assert result.duration_seconds < 3


def test_single_tool_timeout_is_detected_before_whole_agent_timeout(tmp_path):
    command = fake_command(
        tmp_path,
        """
sys.stdin.read()
print(json.dumps({'type':'item.started','item':{'id':'shell-1','type':'command_execution','status':'in_progress'}}),flush=True)
time.sleep(30)
""",
    )
    result = CodexExecAdapter(command=command, tool_timeout_seconds=0.15).run(request(tmp_path))
    assert result.error_type == "EXECUTION_ENVIRONMENT_ERROR"
    assert result.metadata["cleanup_reason"] == "tool_timeout"
    assert result.duration_seconds < 3


def test_failed_turn_is_not_success_even_when_cli_exits_zero(tmp_path):
    command = fake_command(
        tmp_path, "print(json.dumps({'type':'turn.failed','error':{'message':'bad'}}))\n"
    )
    result = CodexExecAdapter(command=command).run(request(tmp_path))
    assert not result.success
    assert result.error_type == "AGENT_ERROR"


def test_existing_final_file_cannot_be_reused_for_completed_turn_recovery(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "final.txt").write_text("done")
    command = fake_command(
        tmp_path,
        """
sys.stdin.read()
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'done'}}),flush=True)
print(json.dumps({'type':'turn.completed'}),flush=True)
time.sleep(30)
""",
    )
    result = CodexExecAdapter(command=command, completion_grace_seconds=0.15).run(request(tmp_path))
    assert not result.success
    assert result.final_text == ""


@pytest.mark.parametrize("parallel", [2, 10])
def test_completion_cleanup_works_under_parallel_process_load(tmp_path, parallel):
    from concurrent.futures import ThreadPoolExecutor

    def execute(index):
        folder = tmp_path / str(index)
        folder.mkdir()
        return CodexExecAdapter(
            command=completed_but_hanging(folder), completion_grace_seconds=0.15
        ).run(request(folder, timeout=10))

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        results = list(pool.map(execute, range(parallel)))
    assert all(result.success for result in results)
    assert all(result.metadata["forced_cleanup"] for result in results)
    assert all(result.duration_seconds < 5 for result in results)
