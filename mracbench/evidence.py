"""Immutable evidence tracking and a single-writer lock for resumable runs."""

import hashlib
import os
from contextlib import contextmanager
from pathlib import Path

from .models import BenchError


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@contextmanager
def run_lock(path: Path):
    lock = path / ".run.lock"
    try:
        with lock.open("x", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))
    except FileExistsError as exc:
        raise BenchError("RESUME_ERROR", f"Run is locked: {lock}") from exc
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


class Evidence:
    def __init__(self, root, known=None):
        self.root = root.resolve()
        self.known = dict(known or {})
        self.empty_workspaces = []

    def path(self, name):
        path = self.root / name
        if Path(name).is_absolute() or not path.resolve().is_relative_to(self.root):
            raise BenchError("PROTOCOL_VIOLATION", f"Evidence path escapes run: {name}")
        if path.is_symlink():
            raise BenchError("PROTOCOL_VIOLATION", f"Symlink evidence is not supported: {name}")
        return path

    def record(self, name):
        if name in self.known:
            raise BenchError("PROTOCOL_VIOLATION", f"Evidence already exists: {name}")
        self.known[name] = digest(self.path(name).read_bytes())

    def check(self):
        try:
            for name, expected in self.known.items():
                if digest(self.path(name).read_bytes()) != expected:
                    raise BenchError("PROTOCOL_VIOLATION", f"Immutable evidence changed: {name}")
            for folder in (
                "input",
                "artifacts",
                "audits",
                "reviews",
                "repairs",
                "closures",
                "pauses",
                "resumptions",
            ):
                for path in (self.root / folder).rglob("*"):
                    if path.is_file() and path.relative_to(self.root).as_posix() not in self.known:
                        raise BenchError(
                            "PROTOCOL_VIOLATION", f"Unregistered evidence added: {path}"
                        )
            for workspace in self.empty_workspaces:
                if workspace.is_symlink() or not workspace.is_dir() or any(workspace.iterdir()):
                    raise BenchError("PROTOCOL_VIOLATION", "Standalone audit workspace changed")
        except OSError as exc:
            raise BenchError("PROTOCOL_VIOLATION", f"Cannot verify evidence: {exc}") from exc

    def capture(self, directory):
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                self.record(path.relative_to(self.root).as_posix())
