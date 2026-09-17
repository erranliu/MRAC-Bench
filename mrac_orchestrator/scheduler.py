import json
import time
from pathlib import Path

from mrac_contracts.execution import (
    OCCUPIED,
    TERMINAL,
    atomic,
    new_id,
    now,
)
from mrac_resources.locks import file_lock
from mrac_resources.repositories import RepoPool


class Scheduler:
    def __init__(self, store, backend, *, total=4, groups=None):
        self.store, self.backend = store, backend
        self.total, self.groups = total, groups or {}
        self.rotation = 0

    def _finish(self, task, attempt, observed):
        state = observed["state"]
        changes = {"last_observation": observed}
        if task["state"] == "CANCEL_REQUESTED" and state != "UNKNOWN":
            state = "CANCELLED"
        if state == "LOST":
            batch = self.store.batch(task["batch_id"])
            actions = []
            try:
                if (Path(task["run_dir"]) / "lifecycle.json").exists():
                    actions = self.backend.inspect(task["run_dir"]).get("allowed_actions", [])
                else:
                    actions = ["start"]
            except (ValueError, OSError):
                pass
            if task["auto_recoveries"] < batch["max_auto_recoveries"] and actions:
                operation = (
                    "recover" if "recover" in actions else "start" if "start" in actions else None
                )
                if operation:
                    state = "RETRY_WAIT"
                    changes.update(
                        auto_recoveries=task["auto_recoveries"] + 1,
                        retry_at=time.time() + min(60, 5 * 2 ** task["auto_recoveries"]),
                        operation=operation,
                        operation_id=new_id("op"),
                    )
                else:
                    state = "FAILED"
            else:
                state = "FAILED"
        public = observed.get("public", {})
        changes.update(
            outcome=public.get("outcome"), error=observed.get("error", public.get("error"))
        )
        with self.store.transaction():
            current = self.store.task(task["batch_id"], task["id"])
            if current.get("attempt_id") != attempt["id"]:
                return
            if current["state"] == "CANCEL_REQUESTED":
                state = "CANCELLED"
            self.store.update(current, state, **changes)
            self.store.db.execute("UPDATE attempts SET state=? WHERE id=?", (state, attempt["id"]))
        if state == "CANCELLED" and hasattr(self.backend, "cancelled"):
            self.backend.cancelled(current)

    def reconcile(self):
        attempts = {a["id"]: a for a in self.store.attempts()}
        for task in self.store.tasks():
            if task["state"] == "CANCELLED" and hasattr(self.backend, "cancelled"):
                public = Path(task["run_dir"]) / "lifecycle.json"
                if public.exists():
                    from mrac_contracts.execution import read_json

                    if read_json(public).get("lifecycle") != "CANCELLED":
                        self.backend.cancelled(task)
            if task["state"] not in OCCUPIED:
                continue
            attempt = attempts[task["attempt_id"]]
            directory = Path(attempt["directory"])
            if task["state"] == "CANCEL_REQUESTED":
                atomic(directory / "cancel.json", {"at": now()})
            try:
                observed = self.backend.observe(task, attempt)
            except (ValueError, OSError) as exc:
                observed = {"state": "UNKNOWN", "error": str(exc)}
            if observed["state"] == "UNKNOWN":
                # Re-dispatch the SAME attempt. Worker OS lock and runner operation journal
                # prevent duplicate execution even if a delayed original worker appears.
                if (
                    not (directory / "started.json").exists()
                    and not (directory / "finished.json").exists()
                ):
                    request_path = directory / "request.json"
                    if not request_path.exists():
                        atomic(request_path, self.backend.request(task, attempt))
                    self.backend.launch(request_path)
                if task["state"] != "RECOVERING" and task["state"] != "CANCEL_REQUESTED":
                    with self.store.transaction():
                        self.store.update(task, "RECOVERING", recovery_reason=observed.get("error"))
            elif observed["state"] == "RUNNING":
                if task["state"] == "CANCEL_REQUESTED":
                    atomic(directory / "cancel.json", {"at": now()})
                elif task["state"] != "RUNNING":
                    with self.store.transaction():
                        self.store.update(task, "RUNNING")
            else:
                self._finish(task, attempt, observed)

    def invalidate(self):
        invalid = RepoPool(self.store.home).invalid_runs()
        tasks = self.store.tasks()
        changed = True
        while changed:
            changed = False
            for task in tasks:
                affected = task["run_dir"] in invalid or any(
                    parent["batch_id"] == task["batch_id"]
                    and parent["group"] == dep["group"]
                    and parent["run_dir"] in invalid
                    for dep in task["dependencies"]
                    for parent in tasks
                )
                if affected and task["run_dir"] not in invalid:
                    invalid.add(task["run_dir"])
                    changed = True
        for task in tasks:
            if task["run_dir"] in invalid and task["evidence_validity"] != "INVALID":
                state = "CANCEL_REQUESTED" if task["state"] in OCCUPIED else task["state"]
                if state in {"QUEUED", "RETRY_WAIT", "PAUSED", "WAITING_INPUT"}:
                    state = "FAILED"
                with self.store.transaction():
                    self.store.update(
                        task,
                        state,
                        evidence_validity="INVALID",
                        error="Shared repository contamination",
                    )

    def tick(self):
        self.reconcile()
        self.invalidate()
        batches = self.store.batches()
        if not batches:
            return
        batches = batches[self.rotation :] + batches[: self.rotation]
        self.rotation = (self.rotation + 1) % len(batches)
        for batch in batches:
            for task in self.store.tasks(batch["id"]):
                if task["state"] not in {"QUEUED", "RETRY_WAIT"}:
                    continue
                if task.get("retry_at", 0) > time.time():
                    continue
                with self.store.transaction():
                    tasks = self.store.tasks()
                    task = self.store.task(batch["id"], task["id"])
                    if task["state"] not in {"QUEUED", "RETRY_WAIT"}:
                        continue
                    parents = [
                        (parent, dep["when"])
                        for dep in task["dependencies"]
                        for parent in tasks
                        if parent["batch_id"] == batch["id"] and parent["group"] == dep["group"]
                    ]
                    impossible = any(
                        p["state"] in TERMINAL
                        and (
                            p["state"] != "COMPLETED"
                            or p["evidence_validity"] != "VALID"
                            or when == "converged"
                            and p.get("outcome") != "CONVERGED"
                        )
                        for p, when in parents
                    )
                    if impossible:
                        self.store.update(task, "SKIPPED", error="Dependency outcome not satisfied")
                        continue
                    if any(p["state"] != "COMPLETED" for p, _ in parents):
                        continue
                    occupied = [t for t in tasks if t["state"] in OCCUPIED]
                    resource = task["resource_group"]
                    batch_occupied = [t for t in occupied if t["batch_id"] == batch["id"]]
                    if (
                        len(occupied) >= self.total
                        or len(batch_occupied) >= batch["concurrency"]["total"]
                        or sum(t["resource_group"] == resource for t in occupied)
                        >= self.groups.get(resource, self.total)
                        or sum(t["resource_group"] == resource for t in batch_occupied)
                        >= batch["concurrency"]["groups"][resource]
                    ):
                        continue
                    attempt_id = new_id("attempt")
                    directory = (
                        self.store.home
                        / "batches"
                        / batch["id"]
                        / "orchestration/workers"
                        / attempt_id
                    )
                    attempt = {
                        "id": attempt_id,
                        "task_id": task["id"],
                        "batch_id": batch["id"],
                        "run_id": task["run_id"],
                        "directory": str(directory),
                        "created_at": now(),
                    }
                    self.store.db.execute(
                        "INSERT INTO attempts VALUES(?,?,?,?,?)",
                        (attempt_id, batch["id"], task["id"], "STARTING", json.dumps(attempt)),
                    )
                    task = self.store.update(task, "STARTING", attempt_id=attempt_id)
                try:
                    request = self.backend.request(task, attempt)
                    atomic(directory / "request.json", request)
                    self.backend.launch(directory / "request.json")
                except Exception as exc:  # noqa: BLE001 -- durable dispatch failure boundary
                    self._finish(
                        task,
                        attempt,
                        {
                            "state": "LOST" if isinstance(exc, OSError) else "FAILED",
                            "error": str(exc),
                        },
                    )
                break  # One dispatch per batch per tick provides round-robin fairness.

    def serve(self):
        from .evidence import export_batch

        control = self.store.home / "control"
        with file_lock(control / "service.lock", blocking=False):
            (control / "stop.json").unlink(missing_ok=True)
            atomic(
                control / "service.json",
                {"started_at": now(), "total": self.total, "groups": self.groups},
            )
            while not (control / "stop.json").exists():
                self.tick()
                for batch in self.store.batches():
                    export_batch(self.store, batch["id"])
                time.sleep(0.5)
