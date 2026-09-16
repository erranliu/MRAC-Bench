import hashlib
import re
from pathlib import Path

import yaml

from .models import BenchError, Case

IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


class UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ValueError("YAML keys must be unique strings")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_yaml(raw: bytes, label: str) -> dict:
    try:
        value = yaml.load(raw.decode("utf-8-sig"), Loader=UniqueLoader)
        if not isinstance(value, dict):
            raise TypeError("expected a YAML mapping")
        return value
    except (UnicodeError, ValueError, TypeError, yaml.YAMLError, RecursionError) as exc:
        raise BenchError("CASE_ERROR", f"{label}: {exc}") from exc


def identifier(value, label: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise BenchError("CASE_ERROR", f"Invalid {label}: expected a simple identifier")
    return value


def positive_int(value, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise BenchError("CASE_ERROR", f"{label} must be a positive integer")
    return value


def string(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchError("CASE_ERROR", f"{label} must be a nonempty string")
    return value


def section(data: dict, key: str) -> dict:
    value = data.get(key)
    if not isinstance(value, dict):
        raise BenchError("CASE_ERROR", f"{key} must be a mapping")
    return value


def read_inside(root: Path, relative: str) -> bytes:
    relative = string(relative, "file")
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root.resolve()):
        raise BenchError("CASE_ERROR", f"File escapes package directory: {relative}")
    try:
        if not path.is_file():
            raise OSError("not a regular file")
        return path.read_bytes()
    except OSError as exc:
        raise BenchError("CASE_ERROR", f"Cannot read {path}: {exc}") from exc


def decode_text(raw: bytes, label: str) -> str:
    try:
        return string(raw.decode("utf-8-sig"), label)
    except UnicodeError as exc:
        raise BenchError("CASE_ERROR", f"{label} must be UTF-8") from exc


def load_case(project: Path, case_id: str) -> Case:
    identifier(case_id, "case id")
    root = (project / "cases" / case_id).resolve()
    if not root.is_relative_to((project / "cases").resolve()):
        raise BenchError("CASE_ERROR", "Case directory escapes cases root")
    raw = read_inside(root, "case.yaml")
    return parse_case(case_id, raw, lambda name: read_inside(root, name))


def parse_case(case_id: str, raw: bytes, read) -> Case:
    """Validate a package, using either live package bytes or saved input snapshots."""
    # Legacy protocol fields are inert historical data. Selection belongs to RunConfig.
    identifier(case_id, "case id")
    data = load_yaml(raw, "case.yaml")
    if data.get("id") != case_id:
        raise BenchError("CASE_ERROR", "Case id must match its directory name")
    repo = section(data, "repository")
    url = string(repo.get("url"), "repository.url")
    if url.startswith("-"):
        raise BenchError("CASE_ERROR", "Invalid repository URL")
    commit = string(repo.get("commit"), "repository.commit")
    if not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", commit):
        raise BenchError("CASE_ERROR", "repository.commit must be a full commit SHA")
    if section(data, "track").get("type") != "spec":
        raise BenchError("CASE_ERROR", "M1 supports only track.type=spec")
    limits = data.get("limits", {})
    if not isinstance(limits, dict):
        raise BenchError("CASE_ERROR", "limits must be a mapping")
    maximum = limits.get("max_audit_rounds")
    if "max_audit_rounds" in limits:
        positive_int(maximum, "limits.max_audit_rounds")
    timeout = positive_int(limits.get("agent_timeout_seconds", 1800), "agent_timeout_seconds")
    task = section(data, "task")
    digest = string(task.get("sha256"), "task.sha256")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise BenchError("CASE_ERROR", "task.sha256 must be a 64-digit hexadecimal SHA-256")
    task_raw = read(task.get("file"))
    if hashlib.sha256(task_raw).hexdigest() != digest.lower():
        raise BenchError(
            "CASE_ERROR", "task.sha256 mismatch: task file bytes differ from the pinned Spec"
        )
    snapshots = {"case.yaml": raw, "task.md": task_raw}
    related_specs = []
    related = data.get("related_specs", [])
    if not isinstance(related, list):
        raise BenchError("CASE_ERROR", "related_specs must be a list")
    seen = set()
    for index, entry in enumerate(related, 1):
        if not isinstance(entry, dict) or set(entry) != {"file", "sha256"}:
            raise BenchError("CASE_ERROR", "Each related Spec requires file and sha256")
        name = string(entry["file"], "related_specs.file")
        if name in seen or name in {task.get("file"), "case.yaml"}:
            raise BenchError("CASE_ERROR", "Duplicate related Spec input")
        seen.add(name)
        content = read(name)
        digest = string(entry["sha256"], "related_specs.sha256")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise BenchError("CASE_ERROR", "Invalid related Spec SHA-256")
        if hashlib.sha256(content).hexdigest() != digest.lower():
            raise BenchError("CASE_ERROR", f"Related Spec SHA-256 mismatch: {name}")
        snapshots[f"related-spec-{index:02d}.md"] = content
        related_specs.append(
            {"file": name, "sha256": digest.lower(), "content": decode_text(content, name)}
        )
    return Case(
        id=case_id,
        version=positive_int(data.get("version"), "case.version"),
        repository_url=url,
        commit=commit.lower(),
        task=decode_text(task_raw, "task"),
        max_audit_rounds=maximum,
        timeout_seconds=timeout,
        snapshots=snapshots,
        related_specs=related_specs,
    )


def case_from_snapshots(case_id: str, snapshots: dict[str, bytes]) -> Case:
    raw = snapshots["case.yaml"]
    data = load_yaml(raw, "case.yaml")
    files = {data["task"]["file"]: snapshots["task.md"]}
    for index, entry in enumerate(data.get("related_specs", []), 1):
        files[entry["file"]] = snapshots[f"related-spec-{index:02d}.md"]
    return parse_case(case_id, raw, files.__getitem__)
