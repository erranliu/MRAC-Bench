import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from test_orchestration import FakeBackend, seed

from mrac_contracts.execution import ContractError
from mrac_orchestrator.plan import submit
from mrac_orchestrator.scheduler import Scheduler
from mrac_orchestrator.store import Store
from mrac_resources.cases import Registry


def test_concurrent_case_registration_has_unique_permanent_numbers(project, tmp_path):
    root = tmp_path / "home"
    processes = []
    for i in range(4):
        code = (
            "from mrac_resources.cases import Registry;"
            f"r=Registry({str(root)!r});"
            f"r.register({str(project / 'cases/sample')!r},'request-{i}',name='example-{i}');r.close()"
        )
        processes.append(
            subprocess.Popen(
                [sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
        )
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, (stdout, stderr)
    registry = Registry(root)
    try:
        rows = registry.list()
        assert [r["number"] for r in rows] == [1, 2, 3, 4]
    finally:
        registry.close()


def test_registration_recovers_after_directory_publish_before_ready(project, tmp_path):
    registry = Registry(tmp_path / "home")
    original = registry.db

    class InterruptedDB:
        def execute(self, sql, values=()):
            if sql.startswith("UPDATE cases SET default_version"):
                raise RuntimeError("commit boundary")
            return original.execute(sql, values)

    registry.db = InterruptedDB()
    try:
        with pytest.raises(RuntimeError, match="commit boundary"):
            registry.register(project / "cases/sample", "request")
        assert (registry.root / "packages/C000001/v1/package/case.yaml").exists()
        registry.db = original
        assert registry.list() == []
        assert registry.register(project / "cases/sample", "request")["number"] == 1
    finally:
        registry.db = original
        registry.close()


@pytest.mark.parametrize(
    "bad", ["cycle", "unknown_dependency", "unknown_field", "duplicate_case", "zero_repeat"]
)
def test_invalid_batch_rejected_before_backend_execution(project, tmp_path, bad):
    root = tmp_path / "home"
    registry = Registry(root)
    registry.register(project / "cases/sample", "register")
    registry.close()
    group = {
        "id": "g",
        "cases": [{"selector": 1}],
        "model_configs": [{"model": "test", "resource_group": "a"}],
        "protocols": ["spec-mrac-v1"],
        "repeat": 1,
    }
    if bad == "cycle":
        group["depends_on"] = [{"group": "g", "when": "completed"}]
    elif bad == "unknown_dependency":
        group["depends_on"] = [{"group": "missing", "when": "completed"}]
    elif bad == "unknown_field":
        group["unknown"] = True
    elif bad == "duplicate_case":
        group["cases"].append({"selector": "C000001"})
    else:
        group["repeat"] = 0
    path = tmp_path / "batch.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "concurrency": {"total": 1, "groups": {"a": 1}},
                "groups": [group],
            }
        ),
        encoding="utf-8",
    )
    store = Store(root)
    try:
        with pytest.raises(ContractError):
            submit(store, object(), path, project, "submit")
        assert store.tasks() == []
    finally:
        store.close()


def test_global_account_limit_spans_batches(tmp_path):
    store = Store(tmp_path / "home")
    try:
        seed(store, "B-one", 1)
        seed(store, "B-two", 1)
        backend = FakeBackend()
        scheduler = Scheduler(store, backend, total=4, groups={"account": 1})
        scheduler.tick()
        scheduler.tick()
        assert len(backend.launched) == 1
        first = store.attempts()[0]
        backend.results[first["id"]] = {"state": "PAUSED", "public": {"outcome": "PAUSED"}}
        scheduler.tick()
        assert len(backend.launched) == 2
        assert sum(t["state"] == "PAUSED" for t in store.tasks()) == 1
    finally:
        store.close()


def test_batch_publication_replay_does_not_reexpand_inputs(project, tmp_path, monkeypatch):
    from mrac_contracts.execution import digest
    from mrac_resources.home import copy_package

    class Freezer:
        calls = 0

        def freeze(self, case, protocol_id, project, directory, settings, **kwargs):
            self.calls += 1
            files = copy_package(case["path"], directory / "bundle")
            return {
                "case": case,
                "settings": settings,
                "bundle_manifest": files,
                "hash": digest(files),
            }

    root = tmp_path / "home"
    registry = Registry(root)
    registry.register(project / "cases/sample", "register")
    registry.close()
    config = {
        "schema_version": 1,
        "concurrency": {"total": 1, "groups": {"a": 1}},
        "groups": [
            {
                "id": "g",
                "cases": [{"selector": 1}],
                "model_configs": [{"model": "test", "resource_group": "a"}],
                "protocols": ["spec-mrac-v1"],
                "repeat": 2,
            }
        ],
    }
    source = tmp_path / "batch.yaml"
    source.write_text(yaml.safe_dump(config), encoding="utf-8")
    store = Store(root)
    backend = Freezer()
    original = store.event

    def crash(*args):
        raise RuntimeError("failure before submit commit")

    monkeypatch.setattr(store, "event", crash)
    try:
        with pytest.raises(RuntimeError):
            submit(store, backend, source, project, "submit")
        assert store.tasks() == []
        assert list((root / "batches").glob("*/orchestration/resolved-plan.json"))
        monkeypatch.setattr(store, "event", original)
        (project / "cases/sample/task.md").write_text("changed externally", encoding="utf-8")
        batch = submit(store, backend, source, project, "submit")
        assert len(batch["tasks"]) == 2
        assert backend.calls == 1
        assert submit(store, backend, source, project, "submit")["id"] == batch["id"]
    finally:
        store.close()


def test_stale_operation_revision_does_not_mutate_task(tmp_path):
    from mrac_orchestrator.operations import operate

    store = Store(tmp_path / "home")
    try:
        seed(store, count=1)
        before = json.dumps(store.task("B-test", "T0"), sort_keys=True)
        with pytest.raises(ContractError, match="revision"):
            operate(store, FakeBackend(), "B-test", "T0", "cancel", "op", expected_revision=100)
        assert json.dumps(store.task("B-test", "T0"), sort_keys=True) == before
    finally:
        store.close()


def test_shared_pollution_keeps_previously_verified_completed_run_valid(project, tmp_path):
    from mrac_resources.repositories import RepoPool

    data = yaml.safe_load((project / "cases/sample/case.yaml").read_bytes())["repository"]
    pool = RepoPool(tmp_path / "home")
    with pool.readonly(data["url"], data["commit"], tmp_path / "completed"):
        pass
    with pool.readonly(data["url"], data["commit"], tmp_path / "active") as item:
        (item[0] / "app.py").write_text("polluted", encoding="utf-8")
    assert pool.invalid_runs() == {str(tmp_path / "active")}


def test_repo_cleanup_event_is_exported_to_batch_once(tmp_path):
    from mrac_contracts.execution import atomic
    from mrac_orchestrator.evidence import export_batch
    from mrac_resources.repositories import RepoPool

    store = Store(tmp_path / "home")
    try:
        seed(store, count=1)
        task = store.task("B-test", "T0")
        with store.transaction():
            store.update(task, "COMPLETED", outcome="CONVERGED")
        pool = RepoPool(store.home)
        run_dir = Path(task["run_dir"])
        checkout = pool.record_run(task["run_id"], run_dir, "B-test")
        checkout.mkdir(parents=True)
        (checkout / "product.txt").write_text("candidate", encoding="utf-8")
        atomic(run_dir / "lifecycle.json", {"lifecycle": "COMPLETED"})
        atomic(run_dir / "seal.json", {"files": {}})
        pool.clean(apply=True)
        export_batch(store, "B-test")
        export_batch(store, "B-test")
        events = [json.loads(r[0]) for r in store.db.execute("SELECT data FROM events")]
        assert sum(e["kind"] == "repo_cleanup" for e in events) == 1
        assert not checkout.exists()
    finally:
        store.close()


def test_checkpoint_inspection_does_not_hold_scheduler_write_transaction(tmp_path):
    from mrac_orchestrator.operations import operate

    store = Store(tmp_path / "home")

    class Backend(FakeBackend):
        def inspect(self, run_dir):
            assert not store.db.in_transaction
            return super().inspect(run_dir)

    try:
        seed(store, count=1)
        with store.transaction():
            store.update(store.task("B-test", "T0"), "PAUSED")
        result = operate(store, Backend(), "B-test", "T0", "continue", "continue-no-lock")
        assert result["state"] == "QUEUED"
    finally:
        store.close()


def test_machine_output_is_utf8_despite_windows_codepage(tmp_path, monkeypatch):
    from mrac_contracts.execution import atomic
    from mrac_orchestrator.backends.mrac import machine

    run = tmp_path / "run-chinese"
    atomic(
        run / "lifecycle.json",
        {
            "schema_version": 1,
            "run_id": run.name,
            "lifecycle": "CANCELLED",
            "infrastructure_terminal": True,
            "error": "保留中文问题与路径",
        },
    )
    monkeypatch.setenv("PYTHONIOENCODING", "cp936")
    assert machine("inspect", "--run-dir", run)["error"] == "保留中文问题与路径"
