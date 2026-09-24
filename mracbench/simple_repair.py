"""Isolated, file-based Spec repair workspaces for Simple v6."""

import difflib
import json
from pathlib import Path

from .evidence import digest
from .models import BenchError


def accepted_file(accepted):
    return (json.dumps(accepted, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def repair_files(case, accepted, seed):
    files = {
        "spec.md": seed,
        "source-spec.md": case.snapshots["task.md"],
        "accepted-findings.json": accepted_file(accepted),
    }
    related = []
    for index, item in enumerate(case.related_specs, 1):
        name = f"related-spec-{index:02d}.md"
        files[name] = case.snapshots[name]
        related.append({"file": item["file"], "path": name, "sha256": item["sha256"]})
    return files, related


def closure_files(case, accepted, previous, candidate, diff):
    return {
        "spec.md": candidate,
        "previous-spec.md": previous,
        "source-spec.md": case.snapshots["task.md"],
        "accepted-findings.json": accepted_file(accepted),
        "diff.txt": diff.encode("utf-8"),
    }


def prepare_workspace(raw: Path, files: dict[str, bytes]):
    workspace = raw / "workspace"
    workspace.mkdir()
    for name, content in files.items():
        (workspace / name).write_bytes(content)
    (raw / "workspace-before.json").write_text(
        json.dumps({name: digest(content) for name, content in files.items()}, indent=2) + "\n",
        encoding="utf-8",
    )


def verify_workspace(workspace: Path, files: dict[str, bytes], *, writable=()):
    if workspace.is_symlink() or not workspace.is_dir():
        raise BenchError("PROTOCOL_VIOLATION", "Repair workspace is missing or linked")
    found = set()
    for path in workspace.rglob("*"):
        if path.is_symlink() or not path.is_file() or path.parent != workspace:
            raise BenchError("PROTOCOL_VIOLATION", "Unexpected repair workspace entry")
        found.add(path.name)
    if found != set(files):
        raise BenchError("PROTOCOL_VIOLATION", "Repair workspace file set changed")
    result = {name: (workspace / name).read_bytes() for name in files}
    for name, original in files.items():
        if name not in writable and result[name] != original:
            raise BenchError("PROTOCOL_VIOLATION", f"Immutable repair input changed: {name}")
    return result


def validate_candidate(previous: bytes, seed: bytes, candidate: bytes):
    if len(candidate) > 8 * 1024 * 1024:
        raise BenchError("REPAIR_INVALID", "Replacement Spec exceeds 8 MiB")
    try:
        text = candidate.decode("utf-8-sig")
        old = previous.decode("utf-8-sig")
    except UnicodeError as exc:
        raise BenchError("REPAIR_INVALID", "Replacement Spec must be UTF-8") from exc
    if candidate == previous or candidate == seed:
        raise BenchError("REPAIR_INVALID", "Repair did not change the candidate Spec")
    if len([line for line in text.splitlines() if line.strip()]) < 2:
        raise BenchError("REPAIR_INVALID", "Replacement Spec is incomplete")
    if any(line.startswith("# ") for line in old.splitlines()) and not any(
        line.startswith("# ") for line in text.splitlines()
    ):
        raise BenchError("REPAIR_INVALID", "Replacement Spec lost its level-one title")
    return text


def unified_spec_diff(previous: bytes, candidate: bytes):
    before = previous.decode("utf-8-sig").splitlines(keepends=True)
    after = candidate.decode("utf-8-sig").splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(before, after, fromfile="previous-spec.md", tofile="spec.md")
    )


def verify_candidate_reads(raw: Path, required: set[str]):
    try:
        events = [
            json.loads(line)
            for line in (raw / "candidate-events.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]
        seen = {
            event.get("arguments", {}).get("path", "spec.md")
            for event in events
            if event.get("tool") == "candidate_read" and event.get("ok") is True
        }
        missing = required - seen
        if missing:
            raise ValueError("Closure did not read: " + ", ".join(sorted(missing)))
    except (OSError, TypeError, ValueError) as exc:
        raise BenchError("CLOSURE_INVALID", f"Missing candidate read evidence: {exc}") from exc
