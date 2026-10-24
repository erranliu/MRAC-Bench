"""Observe persisted JSONL without mixing model completion with process exit."""

import hashlib
import json

TOOL_TYPES = {"command_execution", "shell", "mcp_tool_call", "file_change"}
TIMED_TOOL_TYPES = TOOL_TYPES - {"file_change"}


class InvocationMonitor:
    def __init__(self, start):
        self.start = self.last_event = start
        self.offset = 0
        self.buffer = b""
        self.completed_at = None
        self.failed = False
        self.final_message = None
        self.pending = {}
        self.tool_seconds = 0.0
        self.tool_since = None
        self.failures = {}
        self.event_count = 0

    def read(self, path, now):
        with path.open("rb") as stream:
            stream.seek(self.offset)
            chunk = stream.read()
        self.offset += len(chunk)
        self.buffer += chunk
        lines = self.buffer.split(b"\n")
        self.buffer = lines.pop()
        for line in lines:
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if isinstance(event, dict):
                self.observe(event, now)

    def observe(self, event, now):
        self.event_count += 1
        self.last_event = now
        kind = event.get("type")
        if kind == "turn.started":
            self.completed_at = None
            self.final_message = None
        elif kind == "turn.completed":
            if self.completed_at is None:
                self.completed_at = now
        elif kind in {"turn.failed", "error"}:
            self.failed = True
        item = event.get("item")
        if not isinstance(item, dict):
            return
        if kind == "item.completed" and item.get("type") == "agent_message":
            self.final_message = item.get("text")
        if item.get("type") not in TOOL_TYPES:
            return
        identity = item.get("id")
        if not isinstance(identity, str):
            return
        if kind == "item.started":
            if not self.pending:
                self.tool_since = now
            self.pending.setdefault(
                identity,
                {
                    "type": item["type"],
                    "started": now,
                    **{
                        key: item[key]
                        for key in ("server", "tool")
                        if isinstance(item.get(key), str)
                    },
                },
            )
        elif kind == "item.completed":
            self.pending.pop(identity, None)
            if not self.pending and self.tool_since is not None:
                self.tool_seconds += now - self.tool_since
                self.tool_since = None
            if item.get("status") == "failed":
                # Keep only a fingerprint in diagnostics, never tool output or credentials.
                signature = hashlib.sha256(
                    json.dumps(
                        [
                            item.get("type"),
                            item.get("command"),
                            item.get("server"),
                            item.get("tool"),
                            item.get("arguments"),
                        ],
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()
                self.failures[signature] = self.failures.get(signature, 0) + 1

    def overdue_tool(self, now, limit):
        return next(
            (
                identity
                for identity, tool in self.pending.items()
                if tool["type"] in TIMED_TOOL_TYPES and now - tool["started"] >= limit
            ),
            None,
        )

    def verifies(self, text):
        return not self.verification(text)["failure_reasons"]

    def verification(self, text):
        checks = {
            "turn_completed": self.completed_at is not None,
            "no_failed_event": not self.failed,
            "no_pending_tools": not self.pending,
            "final_message_present": isinstance(self.final_message, str),
            "final_file_nonempty": bool(text.strip()),
            "final_text_matches": (
                isinstance(self.final_message, str) and text.strip() == self.final_message.strip()
            ),
        }
        reasons = {
            "turn_completed": "turn_not_completed",
            "no_failed_event": "failed_event",
            "no_pending_tools": "pending_tools",
            "final_message_present": "missing_final_message",
            "final_file_nonempty": "missing_final_file",
            "final_text_matches": "final_text_mismatch",
        }
        return {
            **checks,
            "failure_reasons": [reasons[name] for name, passed in checks.items() if not passed],
        }

    def snapshot(self, now):
        active_end = self.completed_at if self.completed_at is not None else now
        tool_end = max(self.tool_since or self.start, active_end)
        tools = self.tool_seconds + (
            tool_end - self.tool_since if self.tool_since is not None else 0
        )
        active = active_end - self.start
        tools = min(tools, active)
        warnings = []
        if self.completed_at is None and now - self.last_event >= 300:
            warnings.append("no_stdout_event_for_300_seconds")
        if max(self.failures.values(), default=0) >= 3:
            warnings.append("same_tool_failed_at_least_three_times")
        return {
            "agent_active_seconds": round(active, 3),
            "tool_active_seconds": round(tools, 3),
            "non_tool_seconds": round(max(0, active - tools), 3),
            "post_completion_seconds": round(max(0, now - active_end), 3),
            "completion_observed": self.completed_at is not None,
            "events_observed": self.event_count,
            "pending_tools": [{"id": k, "type": v["type"]} for k, v in self.pending.items()],
            "pending_tool_diagnostics": [
                {
                    "id": identity,
                    "type": tool["type"],
                    "age_seconds": round(max(0, now - tool["started"]), 3),
                    "timeout_applies": tool["type"] in TIMED_TOOL_TYPES,
                    **{key: tool[key] for key in ("server", "tool") if key in tool},
                }
                for identity, tool in self.pending.items()
            ],
            "failed_tools": sum(self.failures.values()),
            "warnings": warnings,
        }
