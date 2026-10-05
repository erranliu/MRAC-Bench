from mracbench.invocation_monitor import InvocationMonitor


def test_overlapping_tools_are_counted_once_and_model_time_is_separate():
    monitor = InvocationMonitor(0)
    monitor.observe({"type": "item.started", "item": {"id": "a", "type": "command_execution"}}, 2)
    monitor.observe({"type": "item.started", "item": {"id": "b", "type": "mcp_tool_call"}}, 4)
    monitor.observe({"type": "item.completed", "item": {"id": "a", "type": "command_execution"}}, 6)
    monitor.observe({"type": "item.completed", "item": {"id": "b", "type": "mcp_tool_call"}}, 8)
    monitor.observe({"type": "turn.completed"}, 10)
    timing = monitor.snapshot(30)
    assert timing["tool_active_seconds"] == 6
    assert timing["non_tool_seconds"] == 4
    assert timing["post_completion_seconds"] == 20


def test_pending_file_change_blocks_recovery_but_is_not_a_shell_timeout():
    monitor = InvocationMonitor(0)
    monitor.observe({"type": "item.started", "item": {"id": "edit", "type": "file_change"}}, 1)
    monitor.observe(
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}, 2
    )
    monitor.observe({"type": "turn.completed"}, 3)
    assert monitor.overdue_tool(1000, 300) is None
    assert not monitor.verifies("done")


def test_split_utf8_events_and_repeated_tool_failure_diagnostics(tmp_path):
    import json

    monitor = InvocationMonitor(0)
    path = tmp_path / "stdout.txt"
    event = json.dumps(
        {"type": "item.completed", "item": {"type": "agent_message", "text": "完成"}},
        ensure_ascii=False,
    ).encode()
    split = event.index("完".encode()) + 1
    path.write_bytes(event[:split])
    monitor.read(path, 1)
    assert monitor.event_count == 0
    with path.open("ab") as stream:
        stream.write(event[split:] + b"\n")
    monitor.read(path, 2)
    for i in range(3):
        monitor.observe(
            {
                "type": "item.completed",
                "item": {
                    "id": str(i),
                    "type": "command_execution",
                    "command": "bad command",
                    "status": "failed",
                },
            },
            3,
        )
    assert "same_tool_failed_at_least_three_times" in monitor.snapshot(4)["warnings"]
    monitor.observe({"type": "turn.completed"}, 4)
    assert monitor.verifies("完成")
    assert not monitor.verifies("different final")


def test_active_command_deadline_does_not_depend_on_other_log_activity():
    monitor = InvocationMonitor(0)
    monitor.observe({"type": "item.started", "item": {"id": "a", "type": "command_execution"}}, 1)
    monitor.observe(
        {"type": "item.completed", "item": {"type": "agent_message", "text": "still working"}}, 301
    )
    assert monitor.overdue_tool(301, 300) == "a"
