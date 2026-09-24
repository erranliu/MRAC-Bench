"""Bounded MCP read/edit tools for one isolated Simple repair candidate."""

import argparse
import hashlib
import json
from pathlib import Path

from .repository_mcp import serve

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_EDIT_BYTES = 256 * 1024


class CandidateWorkspace:
    def __init__(self, workspace: Path, manifest: Path, audit_log: Path, *, writable: bool):
        self.root = workspace.resolve(strict=True)
        if workspace.is_symlink() or not self.root.is_dir():
            raise ValueError("Candidate workspace must be a regular directory")
        self.audit_log = audit_log.resolve()
        if self.audit_log.is_relative_to(self.root):
            raise ValueError("Candidate audit log must be outside the workspace")
        expected = json.loads(manifest.read_text(encoding="utf-8"))
        if (
            not isinstance(expected, dict)
            or not expected
            or any(
                not isinstance(name, str)
                or not name
                or "/" in name
                or "\\" in name
                or not isinstance(value, str)
                or len(value) != 64
                for name, value in expected.items()
            )
        ):
            raise ValueError("Invalid candidate workspace manifest")
        self.expected = expected
        self.writable = writable

    def read_bytes(self, name):
        if name not in self.expected:
            raise ValueError("File is not part of the fixed candidate workspace")
        path = self.root / name
        if path.is_symlink() or not path.is_file() or path.resolve() != path:
            raise ValueError("Candidate file is missing, linked, or outside the workspace")
        content = path.read_bytes()
        if len(content) > MAX_FILE_BYTES:
            raise ValueError("Candidate file exceeds size limit")
        if (name != "spec.md" or not self.writable) and (
            hashlib.sha256(content).hexdigest() != self.expected[name]
        ):
            raise ValueError("Immutable candidate input changed")
        return content

    def check(self):
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError("Candidate workspace changed")
        entries = list(self.root.iterdir())
        if any(path.is_symlink() or not path.is_file() for path in entries):
            raise ValueError("Unexpected candidate workspace entry")
        if {path.name for path in entries} != set(self.expected):
            raise ValueError("Candidate workspace file set changed")
        for name in self.expected:
            self.read_bytes(name)

    def call(self, name, args):
        if not isinstance(args, dict):
            raise TypeError("Tool arguments must be an object")
        self.check()
        if name == "candidate_status":
            content = self.read_bytes("spec.md")
            return {
                "sha256": hashlib.sha256(content).hexdigest(),
                "bytes": len(content),
                "changed": hashlib.sha256(content).hexdigest() != self.expected["spec.md"],
            }
        if name == "candidate_read":
            return self.read(args)
        if name == "candidate_search":
            return self.search(args)
        if name == "candidate_edit" and self.writable:
            return self.edit(args)
        raise ValueError(f"Unknown or unavailable candidate tool: {name}")

    def read(self, args):
        name = args.get("path", "spec.md")
        if not isinstance(name, str):
            raise TypeError("path must be a workspace file name")
        start = args.get("start_line", 1)
        limit = args.get("max_lines", 80)
        if type(start) is not int or start < 1:
            raise ValueError("start_line must be positive")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("max_lines must be between 1 and 200")
        content = self.read_bytes(name)
        lines = content.decode("utf-8-sig").splitlines()
        return {
            "path": name,
            "sha256": hashlib.sha256(content).hexdigest(),
            "start_line": start,
            "lines": [
                {"line": number, "text": line[:2000]}
                for number, line in enumerate(lines[start - 1 : start - 1 + limit], start)
            ],
            "truncated": start - 1 + limit < len(lines),
        }

    def search(self, args):
        name = args.get("path", "spec.md")
        query = args.get("query")
        if not isinstance(name, str) or not isinstance(query, str) or not 1 <= len(query) <= 512:
            raise ValueError("Search needs a workspace path and a short query")
        lines = self.read_bytes(name).decode("utf-8-sig").splitlines()
        matches = [
            {"line": number, "text": line[:2000]}
            for number, line in enumerate(lines, 1)
            if query.casefold() in line.casefold()
        ]
        return {
            "path": name,
            "query": query,
            "matches": matches[:50],
            "truncated": len(matches) > 50,
        }

    def edit(self, args):
        old = args.get("old_text")
        new = args.get("new_text")
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise ValueError("Edit requires nonempty old_text and string new_text")
        if len(old.encode("utf-8")) > MAX_EDIT_BYTES or len(new.encode("utf-8")) > MAX_EDIT_BYTES:
            raise ValueError("Edit text exceeds size limit")
        path = self.root / "spec.md"
        content = self.read_bytes("spec.md")
        bom = content.startswith(b"\xef\xbb\xbf")
        original = content.decode("utf-8-sig")
        crlf = "\r\n" in original and "\n" not in original.replace("\r\n", "")
        normalized = original.replace("\r\n", "\n")
        old = old.replace("\r\n", "\n")
        new = new.replace("\r\n", "\n")
        if normalized.count(old) != 1:
            raise ValueError("old_text must occur exactly once in spec.md")
        updated = normalized.replace(old, new, 1)
        if updated == normalized:
            raise ValueError("Edit did not change spec.md")
        if crlf:
            updated = updated.replace("\n", "\r\n")
        result = (b"\xef\xbb\xbf" if bom else b"") + updated.encode("utf-8")
        if len(result) > MAX_FILE_BYTES:
            raise ValueError("Edited Spec exceeds size limit")
        path.write_bytes(result)
        return {
            "before_sha256": hashlib.sha256(content).hexdigest(),
            "after_sha256": hashlib.sha256(result).hexdigest(),
            "bytes": len(result),
        }

    def record(self, tool, args, result=None, error=None):
        event = {"tool": tool, "arguments": args}
        if error is None:
            event.update(ok=True, result=result)
        else:
            event.update(ok=False, error=str(error))
        self.audit_log.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_log.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


READ_TOOLS = [
    {
        "name": "candidate_read",
        "description": "Read bounded numbered lines from an isolated Spec or repair input file.",
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "max_lines": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "candidate_search",
        "description": "Search one isolated workspace file for a short text query.",
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "candidate_status",
        "description": "Return the Spec candidate hash, byte count, and whether it changed.",
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]

EDIT_TOOL = {
    "name": "candidate_edit",
    "description": "Replace one exact text span in spec.md; only the candidate Spec is writable.",
    "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
    "inputSchema": {
        "type": "object",
        "properties": {"old_text": {"type": "string"}, "new_text": {"type": "string"}},
        "required": ["old_text", "new_text"],
        "additionalProperties": False,
    },
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--audit-log", type=Path, required=True)
    parser.add_argument("--writable", action="store_true")
    args = parser.parse_args()
    reader = CandidateWorkspace(
        args.workspace, args.manifest, args.audit_log, writable=args.writable
    )
    tools = READ_TOOLS + ([EDIT_TOOL] if args.writable else [])
    serve(reader, tools=tools, server_name="mracbench-spec-candidate")


if __name__ == "__main__":
    main()
