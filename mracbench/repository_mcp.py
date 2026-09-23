"""Read-only MCP tools scoped to one verified repository snapshot."""

import argparse
import fnmatch
import hashlib
import json
import subprocess
import sys
from pathlib import Path

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_SEARCH_FILES = 2000
MAX_SEARCH_BYTES = 32 * 1024 * 1024
MAX_SEARCH_RESULTS = 50
MAX_RESULT_LINE = 2000
SOURCE_GLOBS = [
    "*.cs",
    "*.py",
    "*.js",
    "*.jsx",
    "*.ts",
    "*.tsx",
    "*.java",
    "*.go",
    "*.rs",
    "*.cpp",
    "*.h",
]


class RepositoryReader:
    def __init__(self, root: Path, head: str, manifest: Path, audit_log: Path):
        self.root = root.resolve(strict=True)
        self.head = head
        self.audit_log = audit_log.resolve()
        if self.audit_log.is_relative_to(self.root):
            raise ValueError("MCP evidence log must be outside the repository")
        snapshot = json.loads(manifest.read_text(encoding="utf-8"))
        if (
            not isinstance(snapshot, dict)
            or not snapshot
            or not all(
                isinstance(path, str)
                and isinstance(file_hash, str)
                and (len(file_hash) == 64 or file_hash.startswith("symlink:"))
                for path, file_hash in snapshot.items()
            )
        ):
            raise ValueError("Repository manifest does not contain a valid fixed-file snapshot")
        self.files = snapshot

    def actual_head(self):
        proc = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"],
            capture_output=True,
            timeout=15,
            check=False,
        )
        if proc.returncode:
            raise RuntimeError("Could not query repository HEAD")
        observed = proc.stdout.decode("ascii", errors="strict").strip()
        if observed != self.head:
            raise RuntimeError("Repository HEAD drifted from the pinned snapshot")
        return observed

    @staticmethod
    def relative_path(value):
        if not isinstance(value, str) or not value or len(value) > 2048:
            raise ValueError("Expected a repository-relative file path")
        normalized = value.replace("\\", "/")
        parts = normalized.split("/")
        if normalized.startswith("/") or ".." in parts or ":" in parts[0]:
            raise ValueError("Path must stay within the fixed repository")
        return normalized

    def read_bytes(self, relative):
        relative = self.relative_path(relative)
        expected = self.files.get(relative)
        if not expected or expected.startswith("symlink:"):
            raise ValueError("Path is not a regular file in the fixed Git snapshot")
        path = self.root / Path(*relative.split("/"))
        current = self.root
        for part in relative.split("/"):
            current = current / part
            if current.is_symlink():
                raise ValueError("Repository symlinks are not readable through this tool")
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self.root) or not resolved.is_file():
            raise ValueError("Path escapes the repository or is not a file")
        data = resolved.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("File exceeds the read-only MCP size limit")
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("File bytes differ from the fixed Git snapshot")
        return relative, data

    def call(self, name, args):
        if name == "repository_head":
            return {"head": self.actual_head()}
        if name == "repository_search":
            return self.search(args)
        if name == "repository_read":
            return self.read(args)
        raise ValueError(f"Unknown repository tool: {name}")

    def search(self, args):
        query = args.get("query")
        if query is not None and (not isinstance(query, str) or not query or len(query) > 512):
            raise ValueError("Search query must contain 1-512 characters when supplied")
        globs = args.get("globs", SOURCE_GLOBS)
        if (
            not isinstance(globs, list)
            or not globs
            or len(globs) > 12
            or any(not isinstance(item, str) or len(item) > 256 or ".." in item for item in globs)
        ):
            raise ValueError("globs must be a short list of repository-relative patterns")
        max_results = args.get("max_results", 10)
        if type(max_results) is not int or not 1 <= max_results <= MAX_SEARCH_RESULTS:
            raise ValueError(f"max_results must be between 1 and {MAX_SEARCH_RESULTS}")
        candidates = [
            path
            for path in sorted(self.files)
            if not self.files[path].startswith("symlink:")
            and any(fnmatch.fnmatchcase(path, glob) for glob in globs)
        ]
        matches = []
        scanned = 0
        scanned_bytes = 0
        for relative in candidates[:MAX_SEARCH_FILES]:
            scanned += 1
            try:
                _, data = self.read_bytes(relative)
                scanned_bytes += len(data)
                if scanned_bytes > MAX_SEARCH_BYTES:
                    scanned -= 1
                    break
                text = data.decode("utf-8-sig")
            except (OSError, UnicodeError, ValueError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if query is None or query.casefold() in line.casefold():
                    matches.append(
                        {
                            "path": relative,
                            "line": number,
                            "text": line[:MAX_RESULT_LINE],
                        }
                    )
                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break
        return {
            "query": query,
            "matches": matches,
            "scanned_files": scanned,
            "truncated": len(candidates) > scanned or scanned_bytes >= MAX_SEARCH_BYTES,
        }

    def read(self, args):
        relative, data = self.read_bytes(args.get("path"))
        start = args.get("start_line", 1)
        limit = args.get("max_lines", 80)
        if type(start) is not int or start < 1:
            raise ValueError("start_line must be a positive integer")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("max_lines must be between 1 and 200")
        lines = data.decode("utf-8-sig").splitlines()
        selected = [
            {"line": index, "text": line[:MAX_RESULT_LINE]}
            for index, line in enumerate(lines[start - 1 : start - 1 + limit], start)
        ]
        return {
            "path": relative,
            "sha256": hashlib.sha256(data).hexdigest(),
            "start_line": start,
            "lines": selected,
            "truncated": start - 1 + limit < len(lines),
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


TOOLS = [
    {
        "name": "repository_head",
        "description": "Return the observed HEAD and fail if it differs from the pinned commit.",
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "repository_search",
        "description": "Search text files in the fixed Git snapshot; paths cannot leave the repository.",
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 512},
                "globs": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
                "max_results": {"type": "integer", "minimum": 1, "maximum": MAX_SEARCH_RESULTS},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "repository_read",
        "description": "Read bounded line ranges from files tracked in the fixed Git snapshot.",
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "max_lines": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
]


def send(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def serve(reader):
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                continue
            method = request.get("method")
            request_id = request.get("id")
            if method == "notifications/initialized" or method == "notifications/cancelled":
                continue
            if method == "initialize":
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "protocolVersion": request.get("params", {}).get(
                                "protocolVersion", "2024-11-05"
                            ),
                            "capabilities": {"tools": {"listChanged": False}},
                            "serverInfo": {"name": "mracbench-readonly-repository", "version": "1"},
                        },
                    }
                )
            elif method == "ping":
                send({"jsonrpc": "2.0", "id": request_id, "result": {}})
            elif method == "tools/list":
                send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}})
            elif method == "tools/call":
                params = request.get("params", {})
                name = params.get("name")
                args = params.get("arguments", {})
                try:
                    result = reader.call(name, args)
                    reader.record(name, args, result=result)
                    body = {
                        "content": [
                            {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
                        ]
                    }
                except Exception as exc:  # noqa: BLE001 -- report tool failures to the client
                    reader.record(name, args, error=exc)
                    body = {
                        "isError": True,
                        "content": [{"type": "text", "text": str(exc)}],
                    }
                send({"jsonrpc": "2.0", "id": request_id, "result": body})
            elif request_id is not None:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": f"Unsupported method: {method}"},
                    }
                )
        except Exception as exc:  # noqa: BLE001 -- keep stdout protocol-only
            print(f"MCP request failed: {exc}", file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--audit-log", type=Path, required=True)
    args = parser.parse_args()
    reader = RepositoryReader(args.repository, args.head, args.manifest, args.audit_log)
    serve(reader)


if __name__ == "__main__":
    main()
