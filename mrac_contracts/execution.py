import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

VERSION = 1
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "SKIPPED"}
OCCUPIED = {"STARTING", "RUNNING", "RECOVERING", "CANCEL_REQUESTED"}


class ContractError(ValueError):
    pass


def utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def now():
    return datetime.now(UTC).isoformat()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ContractError(f"Invalid identifier: {value!r}")
    return value


def new_id(prefix):
    return prefix + "-" + uuid.uuid4().hex


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value)).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_bytes())


def atomic(path, value, *, raw=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(value if raw else canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        for retry in range(40):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if os.name != "nt" or retry == 39:
                    raise
                # Windows readers can momentarily deny FILE_SHARE_DELETE.
                time.sleep(0.025)
    finally:
        temporary.unlink(missing_ok=True)


def database(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version not in {0, VERSION}:
        db.close()
        raise ContractError("Unsupported database schema version")
    deadline = time.monotonic() + 15
    while True:
        try:
            db.execute("PRAGMA journal_mode=WAL").fetchone()
            break
        except sqlite3.OperationalError as exc:
            # Two first-time openers can race the WAL mode transition even with busy_timeout.
            if (getattr(exc, "sqlite_errorcode", 0) & 0xFF) not in {
                5,
                6,
            } or time.monotonic() >= deadline:
                db.close()
                raise
            time.sleep(0.025)
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=15000")
    db.execute("PRAGMA user_version=1")
    return db


def parse_yaml(raw):
    import yaml

    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ContractError("YAML mappings require unique string keys")
            result[key] = loader.construct_object(value_node, deep=deep)
        return result

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    return yaml.load(raw, Loader=UniqueLoader)


def envelope(value):
    value = {k: v for k, v in value.items() if k != "request_sha256"}
    value["schema_version"] = VERSION
    value["request_sha256"] = digest(value)
    return value


def validate_request(value):
    allowed = {
        "schema_version",
        "request_sha256",
        "run_spec_sha256",
        "operation_id",
        "operation",
        "run_id",
        "attempt_id",
        "run_dir",
        "bundle",
        "bundle_manifest",
        "bench_home",
        "batch_id",
        "settings",
        "code_identity",
        "agent_version",
        "codex_executable",
        "case",
        "expected_revision",
        "input_file",
        "input_sha256",
    }
    required = {
        "schema_version",
        "request_sha256",
        "run_spec_sha256",
        "operation_id",
        "operation",
        "run_id",
        "attempt_id",
        "run_dir",
        "bundle",
        "bundle_manifest",
        "bench_home",
        "settings",
        "code_identity",
        "agent_version",
    }
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        raise ContractError("Execution request contains unknown fields or lacks required fields")
    if type(value.get("schema_version")) is not int or value["schema_version"] != VERSION:
        raise ContractError("Unsupported execution contract version")
    expected = digest({k: v for k, v in value.items() if k != "request_sha256"})
    if value.get("request_sha256") != expected:
        raise ContractError("Execution request hash mismatch")
    for key in ("run_id", "attempt_id", "operation_id"):
        identifier(value[key])
    if value.get("operation") not in {"start", "recover", "continue", "answer", "abort"}:
        raise ContractError("Unknown execution operation")
    for key in ("run_dir", "bundle", "bench_home"):
        if not Path(value[key]).is_absolute():
            raise ContractError(f"{key} must be absolute")
    if Path(value["run_dir"]).name != value["run_id"]:
        raise ContractError("run_dir must match run_id")
    return value


def outcome(status):
    if status in {"CONVERGED", "NON_CONVERGED"}:
        return "COMPLETED"
    return {"PAUSED": "PAUSED", "NEEDS_INPUT": "WAITING_INPUT", "ABORTED": "CANCELLED"}.get(
        status, "RUNNING" if status == "RUNNING" else "FAILED"
    )
