import hashlib
import json
import platform
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import yaml


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_text(path: Path, text: str) -> None:
    from mrac_contracts.execution import atomic

    atomic(path, text.encode("utf-8"), raw=True)


def write_json(path: Path, value: dict) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def git_value(project: Path, *args: str) -> str | None:
    try:
        process = subprocess.run(
            ["git", "-C", str(project), *args],
            capture_output=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return process.stdout.strip() if process.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


class RunStore:
    def __init__(self, root: Path, case_id: str, project: Path, run_id: str | None = None):
        # Do not interpolate an unvalidated case id into a filesystem path.
        label = (
            "".join(c if c.isascii() and (c.isalnum() or c in "-_") else "_" for c in case_id)[:64]
            or "invalid-case"
        )
        if run_id is not None:
            from mrac_contracts.execution import identifier

            identifier(run_id)
        self.run_id = run_id or (
            datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{label}-{uuid.uuid4().hex[:8]}"
        )
        self.path = root.resolve() / self.run_id
        self.path.mkdir(parents=True, exist_ok=run_id is not None)
        for name in ("input", "artifacts", "audits", "raw", "logs"):
            (self.path / name).mkdir()
        self.metadata = {
            "run_id": self.run_id,
            "started_at": utc_now(),
            "ended_at": None,
            "benchmark_commit": git_value(project, "rev-parse", "HEAD"),
            "benchmark_dirty": bool(git_value(project, "status", "--porcelain")),
            "python_version": sys.version,
            "os": platform.platform(),
            "input_sha256": {},
        }
        self.save_metadata()

    def save_metadata(self):
        atomic_text(
            self.path / "run.yaml",
            yaml.safe_dump(self.metadata, allow_unicode=True, sort_keys=False),
        )

    def snapshot(self, name: str, content: bytes):
        with (self.path / "input" / name).open("xb") as stream:
            stream.write(content)
        self.metadata["input_sha256"][name] = hashlib.sha256(content).hexdigest()
        self.save_metadata()

    def artifact(self, name: str, text: str) -> str:
        relative = f"artifacts/{name}"
        with (self.path / relative).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        return relative

    def checkpoint(self, result: dict, stage: str):
        write_json(self.path / "state.json", {"stage": stage, "updated_at": utc_now(), **result})
        self.log(stage)

    def log(self, message: str):
        with (self.path / "logs" / "runner.log").open("a", encoding="utf-8") as stream:
            stream.write(f"{utc_now()} {message}\n")

    def finish(self, result: dict):
        self.metadata["ended_at"] = utc_now()
        self.metadata["status"] = result["status"]
        self.save_metadata()
        self.checkpoint(result, "finished")
        write_json(self.path / "result.json", result)
