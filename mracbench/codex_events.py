"""Interpret optional Codex JSONL metadata and execution failures without I/O."""

import json
from dataclasses import dataclass, field


@dataclass
class CodexEvents:
    usage: dict | None = None
    thread_id: str | None = None
    command_executions: list[dict] = field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None


def summarize_events(
    stdout: str, stderr: str = "", *, error_type=None, error_message=None
) -> CodexEvents:
    summary = CodexEvents(error_type=error_type, error_message=error_message)
    if summary.error_type is None and "blocked by policy" in (stdout + "\n" + stderr).casefold():
        summary.error_type = "EXECUTION_POLICY_ERROR"
        summary.error_message = (
            "Codex could not execute a requested repository command because the "
            "execution policy rejected it; see raw stderr/stdout"
        )
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "turn.completed":
            summary.usage = event.get("usage")
        if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
            summary.thread_id = event["thread_id"]
        item = event.get("item")
        if not isinstance(item, dict) or event.get("type") != "item.completed":
            continue
        error = item.get("error")
        error = error if isinstance(error, dict) else {}
        if (
            item.get("type") == "mcp_tool_call"
            and item.get("status") == "failed"
            and summary.error_type in {None, "AGENT_ERROR"}
            and "timed out awaiting tools/call" in str(error.get("message", ""))
        ):
            summary.error_type = "EXECUTION_ENVIRONMENT_ERROR"
            summary.error_message = (
                f"MCP tool timed out: {item.get('server')}/{item.get('tool')}; see raw stdout"
            )
        if item.get("type") in {"command_execution", "shell"}:
            summary.command_executions.append(
                {
                    "command": str(item.get("command", ""))[:4096],
                    "exit_code": item.get("exit_code"),
                }
            )
            if (
                summary.error_type in {None, "AGENT_ERROR"}
                and item.get("status") == "failed"
                and "Failed to create unified exec process: sandbox provisioning failed"
                in str(item.get("aggregated_output", ""))
            ):
                summary.error_type = "EXECUTION_ENVIRONMENT_ERROR"
                summary.error_message = (
                    "Codex could not provision the sandbox for a repository command; "
                    "see raw stdout and the Windows sandbox setup logs"
                )
    return summary
