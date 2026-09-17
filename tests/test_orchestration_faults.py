import ast
import json
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import StubAgent
from test_exec_flow import audit, first_batch, repair
from test_orchestration import FakeBackend, seed

from mrac_contracts.execution import ContractError, atomic, parse_yaml
from mrac_orchestrator.operations import operate
from mrac_orchestrator.scheduler import Scheduler
from mrac_orchestrator.store import Store
from mrac_resources.cases import Registry
from mrac_resources.repositories import RepoPool
from mracbench.exec_flow import resume_exec
from mracbench.runner import run_case


def test_registration_recovers_published_pending_package(project, tmp_path, monkeypatch):
    import mrac_resources.cases as module

    registry = Registry(tmp_path / "home")
    original = module.atomic

    def crash(path, value):
        original(path, value)
        raise RuntimeError("power loss before publication")

    monkeypatch.setattr(module, "atomic", crash)
    try:
        with pytest.raises(RuntimeError):
            registry.register(project / "cases/sample", "registration")
        assert registry.list() == []
        monkeypatch.setattr(module, "atomic", original)
        result = registry.register(project / "cases/sample", "registration")
        assert result["case_key"] == "C000001"
        assert len(registry.list()) == 1
    finally:
        registry.close()


def test_dispatch_crash_after_claim_reuses_same_attempt(tmp_path):
    class CrashBackend(FakeBackend):
        crashed = False

        def request(self, task, attempt):
            if not self.crashed:
                self.crashed = True
                raise SystemExit("crash after durable claim")
            return super().request(task, attempt)

        def observe(self, task, attempt):
            return {"state": "UNKNOWN"}

    store = Store(tmp_path / "home")
    try:
        seed(store, count=1)
        backend = CrashBackend()
        with pytest.raises(SystemExit):
            Scheduler(store, backend).tick()
        attempt = store.attempts()[0]["id"]
        Scheduler(store, backend).tick()
        assert [a["id"] for a in store.attempts()] == [attempt]
        assert len(backend.launched) == 1
    finally:
        store.close()


def test_completion_transaction_replayed_once(tmp_path, monkeypatch):
    store = Store(tmp_path / "home")
    try:
        seed(store, count=1)
        backend = FakeBackend()
        scheduler = Scheduler(store, backend)
        scheduler.tick()
        attempt = store.attempts()[0]
        backend.results[attempt["id"]] = {"state": "COMPLETED", "public": {"outcome": "CONVERGED"}}
        original = store.event

        def crash(*args):
            original(*args)
            raise RuntimeError("crash before COMMIT")

        monkeypatch.setattr(store, "event", crash)
        with pytest.raises(RuntimeError):
            scheduler.tick()
        assert store.task("B-test", "T0")["state"] == "STARTING"
        monkeypatch.setattr(store, "event", original)
        scheduler.tick()
        scheduler.tick()
        assert store.task("B-test", "T0")["state"] == "COMPLETED"
        events = [json.loads(r[0]) for r in store.db.execute("SELECT data FROM events")]
        assert sum(e.get("to") == "COMPLETED" for e in events) == 1
    finally:
        store.close()


def test_dependency_wait_then_skip_without_blocking_independent_task(tmp_path):
    store = Store(tmp_path / "home")
    try:
        seed(store, count=3)
        with store.transaction():
            store.update(store.task("B-test", "T0"), "PAUSED", group="parent")
            store.update(
                store.task("B-test", "T1"),
                "QUEUED",
                group="child",
                dependencies=[{"group": "parent", "when": "converged"}],
            )
        backend = FakeBackend()
        scheduler = Scheduler(store, backend)
        scheduler.tick()
        assert store.task("B-test", "T1")["state"] == "QUEUED"
        assert store.task("B-test", "T2")["state"] == "STARTING"
        with store.transaction():
            store.update(store.task("B-test", "T0"), "COMPLETED", outcome="NON_CONVERGED")
        scheduler.tick()
        assert store.task("B-test", "T1")["state"] == "SKIPPED"
    finally:
        store.close()


@pytest.mark.parametrize("failure", ["TIMEOUT", "PARSE_ERROR", "AGENT_ERROR", "PROTOCOL_VIOLATION"])
def test_protocol_failures_do_not_automatically_retry(tmp_path, failure):
    store = Store(tmp_path / "home")
    try:
        seed(store, count=1)
        backend = FakeBackend()
        scheduler = Scheduler(store, backend)
        scheduler.tick()
        backend.results[store.attempts()[0]["id"]] = {
            "state": "FAILED",
            "public": {"outcome": failure},
        }
        scheduler.tick()
        scheduler.tick()
        assert store.task("B-test", "T0")["state"] == "FAILED"
        assert len(store.attempts()) == 1
    finally:
        store.close()


def test_cancellation_wins_if_accepted_before_completion(tmp_path):
    store = Store(tmp_path / "home")
    try:
        seed(store, count=1)
        backend = FakeBackend()
        scheduler = Scheduler(store, backend)
        scheduler.tick()
        stale_task = store.task("B-test", "T0")
        attempt = store.attempts()[0]
        operate(store, backend, "B-test", "T0", "cancel", "cancel-before-result")
        scheduler._finish(
            stale_task, attempt, {"state": "COMPLETED", "public": {"outcome": "CONVERGED"}}
        )
        assert store.task("B-test", "T0")["state"] == "CANCELLED"
    finally:
        store.close()


def test_exec_recovery_never_extends_six_audit_budget(config, tmp_path):
    spec = tmp_path / "exec-spec.md"
    spec.write_text("# Execution Spec\n\nSet value to 2.\n", encoding="utf-8")
    config = replace(config, protocol_id="exec-mrac-v1", spec_file=spec)
    run, result = run_case(config, StubAgent(first_batch()))
    assert result["status"] == "PAUSED"
    _, recovered = resume_exec(run, StubAgent([]), recover=True)
    assert recovered["status"] == "PAUSED"
    assert recovered["flow"]["audit_limit"] == 6
    assert recovered["flow"]["budget_extensions"] == []
    _, continued = resume_exec(run, StubAgent([repair, audit(), audit()]))
    assert continued["status"] == "CONVERGED"
    assert continued["flow"]["audit_limit"] == 12


def test_cleanup_requires_terminal_sealed_unreserved_workspace(tmp_path):
    pool = RepoPool(tmp_path / "home")
    run = tmp_path / "home/runs/run-one"
    repo = pool.record_run(run.name, run)
    repo.mkdir(parents=True)
    (repo / "candidate.txt").write_text("work")
    atomic(run / "lifecycle.json", {"run_id": run.name, "lifecycle": "PAUSED"})
    assert pool.clean(apply=True) == []
    atomic(run / "lifecycle.json", {"run_id": run.name, "lifecycle": "FAILED"})
    pool.reserve(run.name)
    with pytest.raises(ContractError, match="pending"):
        pool.release(run, "release")
    pool.reserve(run.name, False)
    pool.release(run, "release")
    assert pool.clean(apply=True) == []  # Evidence not sealed yet.
    atomic(run / "seal.json", {"files": {}})
    assert pool.clean(apply=True)[0]["deleted"]


def test_core_has_no_runner_imports_and_yaml_rejects_duplicate_keys():
    root = Path(__file__).parents[1]
    for name in ("scheduler.py", "store.py", "operations.py", "evidence.py"):
        tree = ast.parse((root / "mrac_orchestrator" / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("mracbench")
    with pytest.raises(ContractError, match="unique"):
        parse_yaml("schema_version: 1\nschema_version: 2\n")
