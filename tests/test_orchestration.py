import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from conftest import CLEAN, SPEC, StubAgent

from mrac_contracts.execution import ContractError, digest, envelope, read_json
from mrac_orchestrator.evidence import export_batch
from mrac_orchestrator.operations import operate
from mrac_orchestrator.scheduler import Scheduler
from mrac_orchestrator.store import Store
from mrac_resources.cases import Registry
from mrac_resources.home import inventory
from mrac_resources.locks import BusyError, file_lock
from mrac_resources.repositories import RepoPool
from mracbench.machine import code_identity, execute, inspect, validate


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.03)
    raise AssertionError("Condition not reached before deadline")


def test_registry_identity_alias_version_and_archive(project, tmp_path):
    registry = Registry(tmp_path / "home")
    try:
        first = registry.register(project / "cases/sample", "create", name="示例 Case")
        assert first["case_key"] == "C000001"
        assert registry.resolve("1")["sha256"] == registry.resolve("示例 case")["sha256"]
        assert (
            registry.register(project / "cases/sample", "create", name="示例 Case")["number"] == 1
        )
        registry.update(1, name="renamed")
        assert registry.resolve("示例 Case")["name"] == "renamed"
        package = project / "cases/sample/case.yaml"
        data = yaml.safe_load(package.read_bytes())
        data["version"] = 2
        package.write_text(yaml.safe_dump(data), encoding="utf-8")
        registry.register(package.parent, "version2", selector="renamed")
        assert registry.resolve(1)["version"] == 2
        assert registry.resolve(1, 1)["version"] == 1
        package.write_text(
            yaml.safe_dump({**data, "metadata": {"changed": True}}), encoding="utf-8"
        )
        with pytest.raises(ContractError, match="overwrite"):
            registry.register(package.parent, "overwrite", selector=1)
        registry.update(1, archived=True)
        with pytest.raises(ContractError, match="archived"):
            registry.resolve(1)
        assert registry.resolve(1, 1, archived=True)["version"] == 1
    finally:
        registry.close()


@pytest.mark.parametrize("name", ["1", "C000003", " x", "x/../y", "x@1", ""])
def test_registry_rejects_ambiguous_names(project, tmp_path, name):
    registry = Registry(tmp_path / "home")
    try:
        with pytest.raises(ContractError):
            registry.register(project / "cases/sample", "create", name=name)
    finally:
        registry.close()


def test_registry_detects_tampered_package(project, tmp_path):
    registry = Registry(tmp_path / "home")
    try:
        item = registry.register(project / "cases/sample", "create")
        (Path(item["path"]) / "task.md").write_text("tampered", encoding="utf-8")
        with pytest.raises(ContractError, match="changed"):
            registry.resolve(1)
    finally:
        registry.close()


def test_shared_repo_concurrent_readers_and_quarantine(project, tmp_path):
    data = yaml.safe_load((project / "cases/sample/case.yaml").read_bytes())
    repo = data["repository"]
    pool = RepoPool(tmp_path / "home")
    with (
        pool.readonly(repo["url"], repo["commit"], tmp_path / "run1") as first,
        pool.readonly(repo["url"], repo["commit"], tmp_path / "run2") as second,
    ):
        assert first[0] == second[0]
        assert pool.clean(apply=False) == []
        (first[0] / "app.py").write_text("polluted", encoding="utf-8")
    assert pool.invalid_runs() == {str(tmp_path / "run1"), str(tmp_path / "run2")}
    with (
        pytest.raises(ContractError, match="quarantined"),
        pool.readonly(repo["url"], repo["commit"], tmp_path / "run3"),
    ):
        pass
    assert pool.clean(apply=True) == []


def test_shared_repo_cleanup_recreates_generation(project, tmp_path):
    repo = yaml.safe_load((project / "cases/sample/case.yaml").read_bytes())["repository"]
    pool = RepoPool(tmp_path / "home")
    with pool.readonly(repo["url"], repo["commit"], tmp_path / "run1") as item:
        previous = item[0]
    assert len(pool.clean()) == 1
    assert previous.exists()
    assert pool.clean(apply=True)[0]["deleted"]
    with pool.readonly(repo["url"], repo["commit"], tmp_path / "run2") as item:
        assert item[0] != previous


def test_os_shared_locks_across_processes(tmp_path):
    lock = tmp_path / "resource.lock"
    ready = tmp_path / "ready"
    code = (
        "from mrac_resources.locks import file_lock; from pathlib import Path; import time\nwith file_lock(Path("
        + repr(str(lock))
        + "),shared=True):\n Path("
        + repr(str(ready))
        + ").touch()\n time.sleep(30)"
    )
    process = subprocess.Popen([sys.executable, "-c", code])
    try:
        wait_until(ready.exists)
        with (
            file_lock(lock, shared=True, blocking=False),
            pytest.raises(BusyError),
            file_lock(lock, blocking=False),
        ):
            pass
    finally:
        process.kill()
        process.wait(timeout=10)
    with file_lock(lock):
        pass


def test_machine_public_contract_and_start_dedup(project, tmp_path):
    settings = validate(
        project, {"case_id": "sample", "protocol_id": "spec-mrac-v1", "model": "test"}
    )
    run_dir = tmp_path / "home/runs/run-test"
    request = envelope(
        {
            "operation": "start",
            "operation_id": "start-op",
            "run_id": run_dir.name,
            "attempt_id": "attempt-test",
            "run_dir": str(run_dir),
            "bundle": str(project),
            "bench_home": str(tmp_path / "home"),
            "bundle_manifest": inventory(project),
            "code_identity": code_identity(),
            "agent_version": "test",
            "settings": settings,
            "run_spec_sha256": digest(settings),
        }
    )
    agent = StubAgent([SPEC, CLEAN, CLEAN])
    result = execute(request, agent)
    assert result["lifecycle"] == "COMPLETED", result
    assert inspect(run_dir)["outcome"] == "CONVERGED"
    assert execute(request, agent)["lifecycle"] == "COMPLETED"
    assert len(agent.requests) == 3
    artifact = result["result"]["final_artifact"]
    (run_dir / artifact).write_text("tampered", encoding="utf-8")
    assert inspect(run_dir)["checkpoint_valid"] is False


@pytest.mark.skipif(os.name != "nt", reason="Windows supervision")
def test_job_creation_and_tree_termination(tmp_path):
    from mrac_orchestrator.supervisor import alive, identity
    from mrac_resources.windows import JobProcess

    child_file = tmp_path / "child.json"
    code = (
        "import subprocess,sys,time,json; from pathlib import Path; "
        "from mrac_orchestrator.supervisor import identity; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        f"Path({str(child_file)!r}).write_text(json.dumps(identity(p.pid))); time.sleep(60)"
    )
    with (tmp_path / "out").open("wb") as out, (tmp_path / "err").open("wb") as err:
        process = JobProcess([sys.executable, "-c", code], out, err, "Local\\test-" + tmp_path.name)
        try:
            wait_until(child_file.exists)
            child = read_json(child_file)
            parent = identity(process.pid)
            assert alive(child) is True
            process.kill()
            wait_until(process.empty)
            assert alive(child) is False
            assert alive(parent) is False
        finally:
            process.close()


class FakeBackend:
    def __init__(self):
        self.launched = []
        self.results = {}

    def request(self, task, attempt):
        return {"attempt_id": attempt["id"]}

    def launch(self, path):
        self.launched.append(path)

    def observe(self, task, attempt):
        return self.results.get(attempt["id"], {"state": "RUNNING"})

    def inspect(self, run_dir):
        return {"allowed_actions": ["continue", "recover", "answer"], "revision": 1}


def seed(store, batch="B-test", count=3):
    execution = {
        "case": {"case_key": "C000001", "name": "Example", "version": 1},
        "settings": {"model": "test", "protocol_id": "test"},
    }
    data = {
        "id": batch,
        "concurrency": {"total": 2, "groups": {"account": 1}},
        "max_auto_recoveries": 1,
    }
    with store.transaction():
        store.db.execute(
            "INSERT INTO batches VALUES(?,?,?,?)", (batch, batch, "hash", json.dumps(data))
        )
        for i in range(count):
            task = {
                "id": f"T{i}",
                "batch_id": batch,
                "state": "QUEUED",
                "revision": 0,
                "group": "group",
                "dependencies": [],
                "resource_group": "account",
                "run_id": f"run-{i}",
                "run_dir": str(store.home / "batches" / batch / "runs" / f"run-{i}"),
                "operation": "start",
                "operation_id": f"op-{i}",
                "execution": execution,
                "evidence_validity": "VALID",
                "auto_recoveries": 0,
                "history": [],
                "repeat": i + 1,
            }
            store.db.execute(
                "INSERT INTO tasks VALUES(?,?,?,?,?)",
                (task["id"], batch, "QUEUED", 0, json.dumps(task)),
            )
    return data


def test_scheduler_limits_pause_release_and_nonconvergence(tmp_path):
    store = Store(tmp_path / "home")
    try:
        seed(store)
        backend = FakeBackend()
        scheduler = Scheduler(store, backend, total=2)
        scheduler.tick()
        scheduler.tick()
        assert len(backend.launched) == 1
        attempt = store.attempts()[0]
        backend.results[attempt["id"]] = {"state": "PAUSED", "public": {"outcome": "PAUSED"}}
        scheduler.tick()
        assert len(backend.launched) == 2
        assert store.task("B-test", "T0")["state"] == "PAUSED"
        second = store.attempts()[1]
        backend.results[second["id"]] = {
            "state": "COMPLETED",
            "public": {"outcome": "NON_CONVERGED"},
        }
        scheduler.tick()
        assert store.task("B-test", "T1")["state"] == "COMPLETED"
        result = operate(store, backend, "B-test", "T0", "continue", "continue-once")
        assert operate(store, backend, "B-test", "T0", "continue", "continue-once") == result
        with pytest.raises(ContractError):
            operate(store, backend, "B-test", "T0", "cancel", "continue-once")
    finally:
        store.close()


def test_event_export_recovers_corrupt_projection_and_preserves_history(tmp_path):
    store = Store(tmp_path / "home")
    try:
        seed(store, count=1)
        backend = FakeBackend()
        operate(store, backend, "B-test", "T0", "cancel", "cancel1")
        export_batch(store, "B-test")
        root = store.home / "batches/B-test/orchestration"
        (root / "events.jsonl").write_bytes(b"{partial")
        (root / "report.md").write_text("broken")
        result = operate(store, backend, "B-test", "T0", "rerun", "rerun1")
        assert result["history"][0]["state"] == "CANCELLED"
        assert result["run_id"] != "run-0"
        export_batch(store, "B-test")
        events = [json.loads(row) for row in (root / "events.jsonl").read_text().splitlines()]
        assert [row["seq"] for row in events] == list(range(1, len(events) + 1))
        assert "CANCELLED" in (root / "report.md").read_text(encoding="utf-8")
    finally:
        store.close()


def test_existing_runner_accepts_explicit_run_id(config):
    from mracbench.runner import run_case

    path, result = run_case(replace(config, run_id="fixed-run"), StubAgent([SPEC, CLEAN, CLEAN]))
    assert path.name == "fixed-run"
    assert result["status"] == "CONVERGED"
