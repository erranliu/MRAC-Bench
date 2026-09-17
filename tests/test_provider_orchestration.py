import json
import os
import time
from pathlib import Path

import pytest
import yaml
from test_orchestration_integration import (
    fake_codex as fake_codex,  # noqa: PLC0414 -- pytest fixture export
)

from mrac_contracts.providers import ProviderError
from mrac_orchestrator.backends.mrac import MRACBackend
from mrac_orchestrator.evidence import export_batch
from mrac_orchestrator.operations import operate
from mrac_orchestrator.plan import submit
from mrac_orchestrator.scheduler import Scheduler
from mrac_orchestrator.store import Store
from mrac_resources.cases import Registry


def prepare(project, tmp_path, count=2):
    root = tmp_path / "home"
    registry = Registry(root)
    registry.register(project / "cases/sample", "register")
    registry.close()
    providers, models = {}, []
    for i in range(count):
        provider = {
            "id": f"provider{i}",
            "base_url": f"https://provider{i}.invalid/v1",
            "env_key": f"MRAC_PROVIDER_{i}_KEY",
            "wire_api": "responses",
            "model_catalog": {"models": [{"slug": "glm-5.3-flash"}]},
        }
        path = tmp_path / f"provider{i}.yaml"
        path.write_text(yaml.safe_dump(provider), encoding="utf-8")
        providers[provider["id"]] = {"file": path.name}
        models.append(
            {"model": "glm-5.3-flash", "provider": provider["id"], "resource_group": "account"}
        )
    data = {
        "schema_version": 1,
        "providers": providers,
        "concurrency": {"total": 2, "groups": {"account": 2}},
        "groups": [
            {
                "id": "g",
                "cases": [{"selector": 1}],
                "model_configs": models,
                "protocols": ["spec-mrac-v2"],
                "repeat": 1,
            }
        ],
    }
    source = tmp_path / "batch.yaml"
    source.write_text(yaml.safe_dump(data), encoding="utf-8")
    return Store(root), source


def drive(store, backend, wanted, timeout=45):
    scheduler = Scheduler(store, backend, total=2)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        scheduler.tick()
        if all(t["state"] in wanted for t in store.tasks()):
            return
        time.sleep(0.1)
    raise AssertionError(store.tasks())


@pytest.mark.skipif(os.name != "nt", reason="Windows Supervisor")
def test_batch_routes_same_model_to_distinct_frozen_providers(
    project, tmp_path, fake_codex, monkeypatch
):
    trace = tmp_path / "routes.jsonl"
    script = Path(fake_codex).parent / "node_modules/@openai/codex/bin/codex.js"
    content = script.read_text(encoding="utf-8")
    insertion = """
const config={};
for(let i=0;i<args.length;i++) if(args[i]==='-c') { const at=args[i+1].indexOf('='); config[args[i+1].slice(0,at)] = JSON.parse(args[i+1].slice(at+1)); }
const provider=config.model_provider;
const prefix='model_providers.'+provider+'.';
if (!process.env[config[prefix+'env_key']]) throw new Error('Missing selected credential');
if (config[prefix+'requires_openai_auth']!==false) throw new Error('Unexpected OpenAI auth');
fs.appendFileSync(TRACE, JSON.stringify({provider,base_url:config[prefix+'base_url'],model:args[args.indexOf('--model')+1]})+'\\n');
""".replace("TRACE", json.dumps(str(trace)))
    content = content.replace("let prompt = '';", insertion + "\nlet prompt = '';")
    script.write_text(content, encoding="utf-8")
    for i in range(2):
        monkeypatch.setenv(f"MRAC_PROVIDER_{i}_KEY", f"private-provider-{i}-credential-123")
    store, source = prepare(project, tmp_path)
    backend = MRACBackend(store.home)
    try:
        batch = submit(store, backend, source, project, "submit", fake_codex)
        frozen = [task["execution"]["settings"]["provider"] for task in batch["tasks"]]
        # The live provider file is no longer consulted after submission.
        (tmp_path / "provider0.yaml").write_text("invalid: changed", encoding="utf-8")
        drive(store, backend, {"COMPLETED", "FAILED"})
        assert [t["state"] for t in store.tasks()] == ["COMPLETED", "COMPLETED"], store.tasks()
        routes = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
        assert len(routes) == 4
        assert {r["provider"] for r in routes} == {"provider0", "provider1"}
        for route in routes:
            assert route["base_url"] == f"https://{route['provider']}.invalid/v1"
            assert route["model"] == "glm-5.3-flash"
        for task, provider in zip(store.tasks(), frozen, strict=True):
            raw = (Path(task["run_dir"]) / "input/provider.json").read_bytes()
            assert json.loads(raw) == provider
            for path in Path(task["run_dir"]).rglob("*"):
                if path.is_file() and not path.name.endswith(".lock"):
                    assert b"private-provider-" not in path.read_bytes()
        export_batch(store, batch["id"])
        report = (store.home / "batches" / batch["id"] / "orchestration/report.md").read_text(
            encoding="utf-8"
        )
        assert "provider0" in report and "provider1" in report
    finally:
        store.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows Supervisor")
def test_missing_key_fails_without_retry_and_recovers_with_frozen_provider(
    project, tmp_path, fake_codex, monkeypatch
):
    monkeypatch.delenv("MRAC_PROVIDER_0_KEY", raising=False)
    store, source = prepare(project, tmp_path, count=1)
    backend = MRACBackend(store.home)
    try:
        batch = submit(store, backend, source, project, "submit", fake_codex)
        drive(store, backend, {"FAILED"})
        failed = store.tasks()[0]
        assert failed["outcome"] == "PROVIDER_ERROR"
        Scheduler(store, backend).tick()
        assert len(store.attempts()) == 1
        result = json.loads((Path(failed["run_dir"]) / "result.json").read_bytes())
        assert result["audit_rounds"] == 0
        monkeypatch.setenv("MRAC_PROVIDER_0_KEY", "restored-credential-123456")
        operate(store, backend, batch["id"], failed["id"], "recover", "recover-provider")
        drive(store, backend, {"COMPLETED", "FAILED"})
        recovered = store.tasks()[0]
        assert recovered["state"] == "COMPLETED", recovered
        assert recovered["run_id"] == failed["run_id"]
        assert len(store.attempts()) == 2
    finally:
        store.close()


def test_unknown_provider_is_rejected_before_submission(project, tmp_path):
    store, source = prepare(project, tmp_path, count=1)
    data = yaml.safe_load(source.read_bytes())
    data["groups"][0]["model_configs"][0]["provider"] = "missing"
    source.write_text(yaml.safe_dump(data), encoding="utf-8")
    try:
        with pytest.raises(ProviderError, match="undeclared"):
            submit(store, object(), source, project, "submit")
        assert store.tasks() == []
    finally:
        store.close()
