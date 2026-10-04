"""Protocol-neutral checkpoints, locks and interrupted invocation recovery."""

import json
import os
from contextlib import ExitStack, contextmanager

from mrac_resources.locks import BusyError, file_lock, marker_lock

from .models import BenchError
from .runs import utc_now, write_json


@contextmanager
def _run_lock(path, acquire, message):
    with ExitStack() as stack:
        try:
            stack.enter_context(acquire(path))
        except BusyError as exc:
            raise BenchError("RESUME_ERROR", message) from exc
        yield


def session_lock(path):
    return _run_lock(
        path / ".repository-run.lock",
        lambda lock_path: file_lock(lock_path, blocking=False),
        "Run is still active",
    )


def run_lock(path):
    lock_path = path / ".run.lock"
    return _run_lock(lock_path, marker_lock, f"Run is locked: {lock_path}")


class CheckpointStore:
    """Write a protocol's canonical state before publishing the common projection."""

    def __init__(self, base, evidence, *, filename, schema_version):
        self.base = base
        self.evidence = evidence
        self.filename = filename
        self.schema_version = schema_version

    def __getattr__(self, name):
        return getattr(self.base, name)

    def checkpoint(self, result, stage):
        write_json(
            self.path / self.filename,
            {
                "schema_version": self.schema_version,
                "result": result,
                "evidence_sha256": self.evidence.known,
                "stage": stage,
                "updated_at": utc_now(),
            },
        )
        self.base.checkpoint(result, stage)

    def finish(self, result):
        self.base.metadata["evidence_sha256"] = self.evidence.known
        self.base.finish(result)
        self.checkpoint(result, "finished")


def read_spec(path):
    try:
        raw = path.read_bytes()
        if not raw.decode("utf-8-sig").strip():
            raise BenchError("AUDIT_STALE", "Spec or supplied input is empty")
        return raw
    except (OSError, UnicodeError) as exc:
        raise BenchError("RESUME_ERROR", f"Cannot read UTF-8 Spec/input: {path}: {exc}") from exc


def pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # Access denied is not proof of termination.
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def unfinished_invocation(store, result):
    call = result["flow"]["last_call"]
    if not call:
        return None
    path = store.path / "raw" / call["stage"] / "invocation.json"
    if not path.exists():
        return None
    invocation = json.loads(path.read_bytes())
    if (
        invocation.get("started")
        and not invocation.get("ended_at")
        and pid_alive(invocation.get("pid"))
    ):
        raise BenchError(
            "RESUME_ERROR", "Previous agent process is still alive; end it before resuming"
        )
    return invocation


def reconcile_invocation(store, result, invocation, *, discard_clean=False):
    """Count a started call once, capture partial evidence, and abandon unfinished audits."""
    flow = result["flow"]
    call = flow["last_call"]
    if call:
        field = f"{call['role']}_rounds"
        if invocation and invocation.get("started") and result[field] == call["counter_before"]:
            result[field] += 1
            if call["role"] == "audit" and flow["active_audit"]:
                result["trajectory"].append(dict(flow["active_audit"]))
        for path in sorted((store.path / "raw" / call["stage"]).rglob("*")):
            name = path.relative_to(store.path).as_posix()
            if path.is_file() and name not in store.evidence.known:
                store.evidence.record(name)
        flow["last_call"] = None
    active = flow["active_audit"]
    if active:
        for row in result["trajectory"]:
            if row["audit_id"] == active["audit_id"]:
                row["status"] = "abandoned"
        flow["active_audit"] = None
    if discard_clean or active:
        flow["clean"] = []
