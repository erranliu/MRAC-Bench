import json

import pytest

from mracbench.codex_command import build_command, command_files, mcp_arguments
from mracbench.codex_events import summarize_events
from mracbench.models import AgentRequest


def stream(*events):
    return "\n".join(json.dumps(event) for event in events)


def shell_failure():
    return {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "status": "failed",
            "exit_code": -1,
            "command": "git rev-parse HEAD",
            "aggregated_output": "Failed to create unified exec process: sandbox provisioning failed",
        },
    }


@pytest.mark.parametrize(
    "error_type,expected",
    [
        (None, "EXECUTION_ENVIRONMENT_ERROR"),
        ("AGENT_ERROR", "EXECUTION_ENVIRONMENT_ERROR"),
        ("TIMEOUT", "TIMEOUT"),
        ("PROVIDER_ERROR", "PROVIDER_ERROR"),
        ("INTERNAL_ERROR", "INTERNAL_ERROR"),
    ],
)
def test_environment_events_preserve_higher_priority_failures(error_type, expected):
    parsed = summarize_events(stream(shell_failure()), error_type=error_type, error_message="prior")
    assert parsed.error_type == expected
    if expected == error_type:
        assert parsed.error_message == "prior"
    assert parsed.command_executions == [{"command": "git rev-parse HEAD", "exit_code": -1}]


def test_policy_failure_precedes_environment_events_and_timeout_precedes_policy_text():
    stdout = stream(shell_failure())
    assert summarize_events(stdout, "blocked by policy").error_type == "EXECUTION_POLICY_ERROR"
    assert (
        summarize_events(stdout, "blocked by policy", error_type="TIMEOUT").error_type == "TIMEOUT"
    )


def test_malformed_lines_unknown_items_and_quoted_failures_do_not_hide_usage_or_session():
    quoted = {"type": "item.completed", "item": {"type": "agent_message", "text": shell_failure()}}
    malformed = {"type": "item.completed", "item": {"type": "mcp_tool_call", "error": "unexpected"}}
    events = "partial JSON\n" + stream(
        [],
        None,
        {"type": "thread.started", "thread_id": 7},
        quoted,
        malformed,
        {"type": "thread.started", "thread_id": "fresh"},
        {"type": "turn.completed", "usage": {"input_tokens": 3}},
    )
    parsed = summarize_events(events)
    assert parsed.thread_id == "fresh" and parsed.usage == {"input_tokens": 3}
    assert parsed.error_type is None and parsed.command_executions == []


def test_actual_mcp_timeout_is_an_environment_error_and_command_metadata_is_bounded():
    parsed = summarize_events(
        stream(
            {
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "status": "failed",
                    "server": "repository",
                    "tool": "read",
                    "error": {"message": "timed out awaiting tools/call"},
                },
            },
            {
                "type": "item.completed",
                "item": {"type": "shell", "command": "x" * 5000, "exit_code": 0},
            },
        )
    )
    assert parsed.error_type == "EXECUTION_ENVIRONMENT_ERROR"
    assert "repository/read" in parsed.error_message
    assert len(parsed.command_executions[0]["command"]) == 4096


@pytest.mark.parametrize(
    "windows,mode", [(False, "elevated"), (True, "elevated"), (True, "unelevated")]
)
@pytest.mark.parametrize("readonly", [True, False])
def test_command_plan_preserves_isolation_and_provider_override_without_io(
    tmp_path, windows, mode, readonly
):
    raw = tmp_path / "not-created"
    request = AgentRequest(
        "中文 $()",
        tmp_path / "project with spaces",
        raw,
        30,
        "model",
        readonly=readonly,
        reasoning_effort="high",
        skip_git_repo_check=True,
        output_schema={"type": "object"},
        mcp_servers={"repo": {"command": "python", "args": ["路径"]}},
    )
    provider = {
        "id": "external",
        "name": "External",
        "base_url": "https://external.invalid/v1",
        "wire_api": "responses",
        "env_key": "PROVIDER_KEY",
        "model_catalog": {"models": []},
    }
    command = build_command(
        ["codex.exe"],
        request,
        tmp_path / "final.txt",
        provider=provider,
        windows=windows,
        windows_sandbox=mode,
        accounting_endpoint="http://127.0.0.1:1234/v1",
    )
    assert command[command.index("--cd") + 1] == str(request.workspace)
    assert command[command.index("--sandbox") + 1] == (
        "read-only" if readonly else "workspace-write"
    )
    assert command[-1] == "-" and "--ephemeral" in command and "--ignore-user-config" in command
    assert "--skip-git-repo-check" in command
    options = [command[i + 1] for i, value in enumerate(command) if value == "-c"]
    assert "features.apps=false" in options and "features.multi_agent=false" in options
    config = {key: json.loads(value) for key, value in (option.split("=", 1) for option in options)}
    assert config["web_search"] == "disabled" and config["approval_policy"] == "never"
    assert config["model_providers.external.base_url"] == "http://127.0.0.1:1234/v1"
    assert config["model_providers.external.requires_openai_auth"] is False
    assert ("windows.sandbox" in config) == windows
    if windows:
        assert config["windows.sandbox"] == mode
    assert command_files(request, provider) == {
        raw / "output-schema.json": request.output_schema,
        raw / "model-catalog.json": provider["model_catalog"],
    }
    assert not raw.exists()


@pytest.mark.parametrize(
    "server",
    [
        {"command": ""},
        {"command": "python", "args": "wrong"},
        {"command": "python", "args": [3]},
        {"command": "python", "unknown": True},
    ],
)
def test_command_builder_rejects_invalid_mcp_configuration(server):
    with pytest.raises(ValueError):
        mcp_arguments({"repo": server})
