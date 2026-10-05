"""UTF-8 tools bound to the sole editable Spec in an isolated checkout."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from mrac_contracts.execution import atomic

from .repository_mcp import RepositoryReader, serve

MAX_BYTES = 8 * 1024 * 1024


def server_config(workspace, name, audit_log):
    return {
        "mrac_spec_file": {
            "command": sys.executable,
            "args": [
                "-B",
                "-m",
                "mracbench.spec_file_mcp",
                "--workspace",
                str(workspace),
                "--spec-path",
                name,
                "--audit-log",
                str(audit_log),
            ],
            "startup_timeout_sec": 20,
            "tool_timeout_sec": 45,
        }
    }


class SpecFile:
    def __init__(self, workspace, name, audit_log):
        self.root = workspace.resolve(strict=True)
        self.name = RepositoryReader.relative_path(name)
        if ":" in self.name or any(
            part.casefold() in {"", ".", ".git"} for part in self.name.split("/")
        ):
            raise ValueError("Invalid Spec path")
        self.target = self.root / self.name
        self.audit_log = audit_log.resolve()
        if self.audit_log.is_relative_to(self.root):
            raise ValueError("Tool evidence must stay outside the checkout")

    def read_bytes(self):
        if self.root.is_symlink() or self.root.resolve() != self.root:
            raise ValueError("Checkout root changed")
        current = self.root
        for part in self.name.split("/"):
            current = current / part
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                raise ValueError("Spec path crosses a filesystem link")
        if not current.is_file() or not current.resolve().is_relative_to(self.root):
            raise ValueError("Spec is missing or outside the checkout")
        data = current.read_bytes()
        if len(data) > MAX_BYTES:
            raise ValueError("Spec exceeds 8 MiB")
        data.decode("utf-8-sig")
        return data

    def call(self, name, args):
        if not isinstance(args, dict):
            raise TypeError("Arguments must be an object")
        allowed = (
            {"start_line", "max_lines"}
            if name == "spec_read"
            else {"expected_sha256", "old_text", "new_text"}
        )
        if set(args) - allowed:
            raise ValueError("Unexpected Spec file tool argument")
        content = self.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        if name == "spec_read":
            start, limit = args.get("start_line", 1), args.get("max_lines", 100)
            if (
                type(start) is not int
                or start < 1
                or type(limit) is not int
                or not 1 <= limit <= 200
            ):
                raise ValueError("Read needs a positive start_line and 1-200 max_lines")
            lines = content.decode("utf-8-sig").splitlines()
            return {
                "sha256": sha,
                "path": self.name,
                "total_lines": len(lines),
                "lines": [
                    {"line": i, "text": line}
                    for i, line in enumerate(lines[start - 1 : start - 1 + limit], start)
                ],
                "truncated": start - 1 + limit < len(lines),
            }
        if name != "spec_edit":
            raise ValueError("Unknown Spec file tool")
        if args.get("expected_sha256") != sha:
            raise ValueError("Spec changed; read it again before editing")
        old, new = args.get("old_text"), args.get("new_text")
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise ValueError("Edit needs nonempty old_text and string new_text")
        text = content.decode("utf-8-sig")
        crlf = "\r\n" in text and "\n" not in text.replace("\r\n", "")
        text, old, new = (value.replace("\r\n", "\n") for value in (text, old, new))
        if text.count(old) != 1 or old == new:
            raise ValueError("old_text must occur exactly once and the edit must change it")
        edited = text.replace(old, new, 1)
        if crlf:
            edited = edited.replace("\n", "\r\n")
        data = (b"\xef\xbb\xbf" if content.startswith(b"\xef\xbb\xbf") else b"") + edited.encode(
            "utf-8"
        )
        if len(data) > MAX_BYTES:
            raise ValueError("Edited Spec exceeds 8 MiB")
        if self.read_bytes() != content:
            raise ValueError("Spec changed during the edit")
        atomic(self.target, data, raw=True)
        return {
            "before_sha256": sha,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
        }

    def record(self, tool, args, result=None, error=None):
        self.audit_log.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "tool": tool,
            "arguments": args,
            "ok": error is None,
            "result": result,
            "error": str(error) if error else None,
        }
        with self.audit_log.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")


TOOLS = [
    {
        "name": "spec_read",
        "description": "Read numbered UTF-8 Spec lines and current SHA-256.",
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {
                "start_line": {"type": "integer", "minimum": 1},
                "max_lines": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "spec_edit",
        "description": "Replace one exact UTF-8 Spec span using its current hash. Preserves BOM and CRLF; only the designated Spec is writable.",
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {
                "expected_sha256": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["expected_sha256", "old_text", "new_text"],
            "additionalProperties": False,
        },
    },
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--spec-path", required=True)
    parser.add_argument("--audit-log", type=Path, required=True)
    args = parser.parse_args()
    serve(
        SpecFile(args.workspace, args.spec_path, args.audit_log),
        tools=TOOLS,
        server_name="mracbench-spec-file",
    )


if __name__ == "__main__":
    main()
