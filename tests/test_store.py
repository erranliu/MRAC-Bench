import pytest
from test_orchestration import seed

from mrac_contracts.execution import ContractError
from mrac_orchestrator.store import Store


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "home")
    seed(store, count=2)
    yield store
    store.close()


def attempt(task, name):
    return {"id": name, "batch_id": task["batch_id"], "task_id": task["id"]}


def test_single_task_lookup_does_not_decode_unrelated_task_data(store):
    wanted = store.task("B-test", "T0")
    store.db.execute("UPDATE tasks SET data='corrupt' WHERE batch_id=? AND id=?", ("B-test", "T1"))
    assert store.task("B-test", "T0") == wanted
    assert store.task("other-batch", "T0") is None
    assert store.task("B-test", "missing") is None


def test_claim_rejects_missing_transaction_and_rolls_back_stale_revision(store):
    task = store.task("B-test", "T0")
    with pytest.raises(ContractError, match="transaction"):
        store.claim(task, attempt(task, "A1"))
    assert store.attempts() == []
    with store.transaction():
        claimed = store.claim(task, attempt(task, "A1"))
    assert claimed["revision"] == task["revision"] + 1
    with pytest.raises(ContractError, match="revision"), store.transaction():
        store.claim(task, attempt(task, "stale"))
    assert [a["id"] for a in store.attempts()] == ["A1"]
    assert store.task("B-test", "T0") == claimed


def test_attempt_completion_rolls_back_both_records_on_event_failure(store, monkeypatch):
    task = store.task("B-test", "T0")
    with store.transaction():
        claimed = store.claim(task, attempt(task, "A1"))
    original = store.event
    monkeypatch.setattr(
        store, "event", lambda *args: (_ for _ in ()).throw(RuntimeError("event write"))
    )
    with pytest.raises(RuntimeError, match="event write"), store.transaction():
        store.finish_attempt(claimed, "A1", "COMPLETED")
    assert store.task("B-test", "T0")["state"] == "STARTING"
    assert store.attempts()[0]["state"] == "STARTING"
    monkeypatch.setattr(store, "event", original)
    with store.transaction():
        store.finish_attempt(claimed, "A1", "COMPLETED")
    assert store.task("B-test", "T0")["state"] == store.attempts()[0]["state"] == "COMPLETED"


def test_operation_replay_distinguishes_empty_result_from_missing_and_checks_hash(store):
    assert store.operation_result("op", "hash") is None
    with store.transaction():
        store.record_operation("op", "hash", {})
    assert store.operation_result("op", "hash") == {}
    with pytest.raises(ContractError, match="different request"):
        store.operation_result("op", "changed")


def test_resource_import_is_atomic_idempotent_and_batch_scoped(store):
    event = {"id": "resource-1", "kind": "repo_cleanup", "resource": {"run_dir": "run"}}
    with pytest.raises(RuntimeError), store.transaction():
        store.import_resource_event("B-test", event)
        raise RuntimeError("crash")
    assert store.events("B-test") == []
    with store.transaction():
        assert store.import_resource_event("B-test", event)
        assert not store.import_resource_event("B-test", event)
        assert store.import_resource_event("B-other", event)
    assert len(store.events("B-test")) == len(store.events("B-other")) == 1
    assert store.events("B-test")[0]["event_id"] == "B-test:1"


def test_batch_publication_is_atomic_and_request_lookup_is_indexed(store, monkeypatch):
    task = {**store.task("B-test", "T0"), "batch_id": "B-new"}
    plan = {"id": "B-new", "request_id": "new", "request_hash": "hash", "tasks": [task]}
    monkeypatch.setattr(store, "event", lambda *args: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        store.publish_batch(plan)
    assert store.batch_request("new") is None
    assert store.tasks("B-new") == []
