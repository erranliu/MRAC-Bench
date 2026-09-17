import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from test_orchestration import wait_until

from mrac_contracts.execution import atomic, read_json
from mrac_orchestrator.backends.mrac import MRACBackend
from mrac_orchestrator.evidence import export_batch
from mrac_orchestrator.plan import submit
from mrac_orchestrator.scheduler import Scheduler
from mrac_orchestrator.store import Store
from mrac_orchestrator.supervisor import alive
from mrac_resources.cases import Registry
from mrac_resources.repositories import RepoPool


@pytest.fixture
def fake_codex(tmp_path):
    if not shutil.which("node"):
        pytest.skip("Node required for the fake standard npm Codex shim")
    directory = tmp_path / "fake-cli"
    script = directory / "node_modules/@openai/codex/bin/codex.js"
    script.parent.mkdir(parents=True)
    script.write_text(
        r"""
const fs = require('fs');
const args = process.argv.slice(2);
if (args.includes('--version')) { console.log('codex-test 1'); process.exit(0); }
let prompt = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => prompt += chunk);
process.stdin.on('end', () => {
  const output = args[args.indexOf('--output-last-message') + 1];
  const cwd = args[args.indexOf('--cd') + 1];
  let result;
  const marker = 'INPUT JSON:' + String.fromCharCode(10);
  if (prompt.includes(marker)) {
    const data = JSON.parse(prompt.split(marker)[1]);
    if (data.audit_id) {
      result = {audit_id:data.audit_id, findings:[]};
      if (data.candidate_sha256) Object.assign(result, {spec_sha256:data.spec_sha256,candidate_sha256:data.candidate_sha256});
    } else if (data.execution_spec !== undefined) {
      fs.writeFileSync(require('path').join(cwd,'app.py'), 'value = 2\n');
      result = {disposition:'complete',summary:'Changed value',validation:[{command:'fixture',status:'not_run',evidence:'Stub process'}]};
    } else {
      result = output.includes('generate') ? ['# Spec','','Implement the requested behavior.',''].join(String.fromCharCode(10)) : {status:'clean',issues:[]};
    }
  } else {
    result = output.includes('generate') ? ['# Spec','','Implement the requested behavior.',''].join(String.fromCharCode(10)) : {status:'clean',issues:[]};
  }
  setTimeout(() => {
    console.log(JSON.stringify({type:'thread.started',thread_id:require('crypto').randomUUID()}));
    fs.writeFileSync(output, typeof result === 'string' ? result.replaceAll('\\n','\n') : JSON.stringify(result));
    console.log(JSON.stringify({type:'turn.completed',usage:{input_tokens:1,output_tokens:1}}));
  }, 200);
});
""",
        encoding="utf-8",
    )
    shim = directory / "codex.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")
    return str(shim)


def batch_file(project, tmp_path, protocol="spec-mrac-v1", repeat=2):
    data = {
        "schema_version": 1,
        "name": "integration",
        "concurrency": {"total": 2, "groups": {"test": 2}},
        "groups": [
            {
                "id": "experiments",
                "cases": [{"selector": "C000001"}],
                "model_configs": [{"model": "test", "resource_group": "test"}],
                "protocols": [protocol],
                "repeat": repeat,
            }
        ],
    }
    if protocol == "exec-mrac-v1":
        spec = tmp_path / "exec-spec.md"
        spec.write_text("# Execution Spec\n\nSet value to 2.\n", encoding="utf-8")
        data["groups"][0]["execution_spec_file"] = str(spec)
    path = tmp_path / "batch.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.mark.skipif(os.name != "nt", reason="Windows process supervision")
@pytest.mark.parametrize("protocol", ["spec-mrac-v1", "spec-mrac-v2", "exec-mrac-v1"])
def test_real_process_batch_and_restart(project, tmp_path, fake_codex, protocol):
    root = tmp_path / "home"
    registry = Registry(root)
    registry.register(project / "cases/sample", "register")
    registry.close()
    store = Store(root)
    backend = MRACBackend(root)
    try:
        source = batch_file(project, tmp_path, protocol)
        batch = submit(store, backend, source, project, "submit", fake_codex)
        assert submit(store, backend, source, project, "submit", fake_codex)["id"] == batch["id"]
        scheduler = Scheduler(store, backend, total=2)
        scheduler.tick()
        scheduler.tick()
        assert len(store.attempts()) == 2
        # Drop the scheduler connection; independent supervisors keep running.
        store.close()
        store = Store(root)
        scheduler = Scheduler(store, backend, total=2)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            scheduler.tick()
            if all(t["state"] in {"COMPLETED", "FAILED", "CANCELLED"} for t in store.tasks()):
                break
            time.sleep(0.1)
        tasks = store.tasks()
        assert [t["state"] for t in tasks] == ["COMPLETED", "COMPLETED"], tasks
        assert [t["outcome"] for t in tasks] == ["CONVERGED", "CONVERGED"]
        assert len(store.attempts()) == 2
        resources = RepoPool(root).list()
        shared = [r for r in resources if "key" in r]
        if protocol == "exec-mrac-v1":
            paths = [Path(t["run_dir"]) for t in tasks]
            results = [read_json(p / "result.json") for p in paths]
            assert results[0]["final_checkout"] != results[1]["final_checkout"]
            assert not shared
        else:
            assert len(shared) == 1
            assert len(shared[0]["users"]) == 2
        assert export_batch(store, batch["id"])["state"] == "FINISHED"
    finally:
        store.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows process supervision")
def test_supervisor_crash_kills_runner_and_grandchild(tmp_path):
    request_file = tmp_path / "worker/request.json"
    child_file = tmp_path / "grandchild.json"
    atomic(
        request_file,
        {
            "attempt_id": "crash-test-" + tmp_path.name,
            "run_id": "run-crash",
            "request_sha256": "fixture",
            "bench_home": str(tmp_path / "home"),
            "run_dir": str(tmp_path / "home/runs/run-crash"),
        },
    )
    runner_code = (
        "import subprocess,sys,time,json;from pathlib import Path;"
        "from mrac_orchestrator.supervisor import identity;"
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        f"Path({str(child_file)!r}).write_text(json.dumps(identity(p.pid)));time.sleep(60)"
    )
    code = f"from mrac_orchestrator.supervisor import run;run({str(request_file)!r}, {[sys.executable, '-c', runner_code]!r})"
    supervisor = subprocess.Popen([sys.executable, "-c", code])
    try:
        wait_until(child_file.exists)
        child = read_json(child_file)
        wait_until(lambda: "runner" in read_json(request_file.parent / "started.json"))
        runner = read_json(request_file.parent / "started.json")["runner"]
        supervisor.kill()
        supervisor.wait(timeout=10)
        wait_until(lambda: alive(child) is False)
        wait_until(lambda: alive(runner) is False)
    finally:
        if supervisor.poll() is None:
            supervisor.kill()
        supervisor.wait(timeout=10)
