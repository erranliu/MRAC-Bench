import json
from pathlib import Path

from mrac_contracts.execution import OCCUPIED, ContractError, atomic, digest, identifier, new_id


def operate(
    store,
    backend,
    batch_id,
    task_id,
    action,
    operation_id,
    *,
    expected_revision=None,
    input_file=None,
):
    for value in (batch_id, task_id, operation_id):
        identifier(value)
    request_hash = digest(
        {
            "batch": batch_id,
            "task": task_id,
            "action": action,
            "input": digest(Path(input_file).read_bytes()) if input_file else None,
        }
    )
    old = store.db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
    if old:
        if old["request_hash"] != request_hash:
            raise ContractError("Operation ID reused with different request")
        return json.loads(old["result"])
    snapshot = store.task(batch_id, task_id)
    if not snapshot:
        raise ContractError("Unknown task")
    observed_revision = snapshot["revision"] if expected_revision is None else expected_revision
    public = None
    if action in {"recover", "continue", "answer"}:
        # Checkout/evidence inspection may be expensive. Never hold the scheduler writer lock.
        public = backend.inspect(snapshot["run_dir"])
    with store.transaction():
        old = store.db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
        if old:
            if old["request_hash"] != request_hash:
                raise ContractError("Operation ID reused with different request")
            return json.loads(old["result"])
        task = store.task(batch_id, task_id)
        if not task:
            raise ContractError("Unknown task")
        if observed_revision != task["revision"]:
            raise ContractError("Task revision conflict")
        if action == "cancel":
            if task["state"] in {"COMPLETED", "FAILED", "CANCELLED", "SKIPPED"}:
                result = task
            else:
                result = store.update(
                    task,
                    "CANCEL_REQUESTED" if task["state"] in OCCUPIED else "CANCELLED",
                    cancel_operation_id=operation_id,
                )
        elif action == "rerun":
            if task["state"] in OCCUPIED or task["state"] in {"QUEUED", "RETRY_WAIT"}:
                raise ContractError("Cannot rerun an active task")
            run_id = new_id("run")
            history = [*task["history"], {k: v for k, v in task.items() if k != "history"}]
            result = store.update(
                task,
                "QUEUED",
                run_id=run_id,
                run_dir=str(Path(task["run_dir"]).parent / run_id),
                operation="start",
                operation_id=operation_id,
                history=history,
                auto_recoveries=task["auto_recoveries"],
                retry_at=0,
                attempt_id=None,
                input_file=None,
                evidence_validity="VALID",
                outcome=None,
                error=None,
            )
        elif action in {"recover", "continue", "answer"}:
            if task["state"] in OCCUPIED or task["state"] in {
                "QUEUED",
                "RETRY_WAIT",
                "CANCELLED",
                "COMPLETED",
                "SKIPPED",
            }:
                raise ContractError("Task is not eligible for this operation")
            if action not in public.get("allowed_actions", []):
                raise ContractError("Runner does not allow this operation at its checkpoint")
            if hasattr(backend, "reserve"):
                backend.reserve(task)
            changes = {"operation": action, "operation_id": operation_id, "retry_at": 0}
            if action == "answer":
                if not input_file:
                    raise ContractError("Answer file is required")
                content = Path(input_file).read_bytes()
                if not content.decode("utf-8-sig").strip():
                    raise ContractError("Answer must be nonempty UTF-8")
                target = (
                    store.home
                    / "batches"
                    / batch_id
                    / "orchestration/operations"
                    / operation_id
                    / "answer.md"
                )
                atomic(target, content, raw=True)
                changes.update(input_file=str(target), input_sha256=digest(content))
            result = store.update(task, "QUEUED", **changes)
        else:
            raise ContractError("Unknown operation")
        store.db.execute(
            "INSERT INTO operations VALUES(?,?,?)", (operation_id, request_hash, json.dumps(result))
        )
        store.event(
            batch_id,
            "operation",
            {"operation_id": operation_id, "task_id": task_id, "action": action},
        )
    if action == "cancel" and result["state"] == "CANCELLED" and hasattr(backend, "cancelled"):
        backend.cancelled(result)
    return result
