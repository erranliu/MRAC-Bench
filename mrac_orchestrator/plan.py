import itertools
import json
from pathlib import Path

from mrac_contracts.execution import (
    ContractError,
    atomic,
    digest,
    identifier,
    new_id,
    now,
    parse_yaml,
    read_json,
)
from mrac_contracts.providers import ProviderError, load_providers
from mrac_resources.cases import Registry
from mrac_resources.home import checked
from mrac_resources.locks import file_lock


def keys(value, allowed):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ContractError(f"Unknown/invalid configuration fields; expected {allowed}")


def positive(value):
    if type(value) is not int or value < 1:
        raise ContractError("Expected positive integer")
    return value


def submit(store, backend, source, project, request_id, codex="codex"):
    identifier(request_id)
    source, project = Path(source).resolve(), Path(project).resolve()
    raw = source.read_bytes()
    request_hash = digest({"bytes": raw.hex(), "project": str(project), "codex": codex})
    with file_lock(store.home / "control/submit.lock"):
        existing = store.db.execute(
            "SELECT id,request_hash FROM batches WHERE request_id=?", (request_id,)
        ).fetchone()
        if existing:
            if existing["request_hash"] != request_hash:
                raise ContractError("Batch request ID reused with different configuration")
            return store.batch(existing["id"])
        batch_id = "B-" + digest(request_id)[:24]
        directory = checked(store.home, store.home / "batches" / batch_id)
        orchestration = directory / "orchestration"
        plan_path = orchestration / "resolved-plan.json"
        if plan_path.exists():
            plan = read_json(plan_path)
            if plan["request_hash"] != request_hash:
                raise ContractError("Uncommitted batch request differs")
        else:
            data = parse_yaml(raw)
            keys(data, {"schema_version", "name", "concurrency", "retry", "groups", "providers"})
            if data.get("schema_version") != 1:
                raise ContractError("Unsupported batch schema")
            providers = load_providers(data.get("providers", {}), source.parent)
            concurrency = data.get("concurrency", {"total": 1, "groups": {}})
            keys(concurrency, {"total", "groups"})
            positive(concurrency["total"])
            for value in concurrency.get("groups", {}).values():
                positive(value)
            retry = data.get("retry", {"max_auto_recoveries": 1})
            keys(retry, {"max_auto_recoveries"})
            maximum = retry.get("max_auto_recoveries", 1)
            if type(maximum) is not int or maximum < 0:
                raise ContractError("Invalid automatic recovery limit")
            groups = data.get("groups", [])
            names = [identifier(g["id"]) for g in groups]
            if not groups or len(names) != len(set(names)):
                raise ContractError("Batch requires unique task groups")
            dependencies = {}
            for group in groups:
                keys(
                    group,
                    {
                        "id",
                        "cases",
                        "model_configs",
                        "protocols",
                        "repeat",
                        "depends_on",
                        "timeout_seconds",
                        "max_audit_rounds",
                        "execution_spec_file",
                    },
                )
                dependencies[group["id"]] = group.get("depends_on", [])
                for dep in dependencies[group["id"]]:
                    keys(dep, {"group", "when"})
                    if dep["group"] not in names or dep["when"] not in {"completed", "converged"}:
                        raise ContractError("Invalid dependency")

            def visit(name, path):
                if name in path:
                    raise ContractError("Dependency cycle")
                for dep in dependencies[name]:
                    visit(dep["group"], path | {name})

            for name in names:
                visit(name, set())
            registry = Registry(store.home)
            tasks = []
            try:
                for group in groups:
                    resolved, seen = [], set()
                    for selection in group["cases"]:
                        keys(selection, {"selector", "version"})
                        case = registry.resolve(selection["selector"], selection.get("version"))
                        key = (case["number"], case["version"])
                        if key in seen:
                            raise ContractError("Duplicate case selection; use repeat")
                        seen.add(key)
                        resolved.append(case)
                    for model in group["model_configs"]:
                        keys(model, {"model", "reasoning_effort", "resource_group", "provider"})
                        selection = model.get("provider", "openai")
                        if (
                            not isinstance(selection, str)
                            or selection != "openai"
                            and selection not in providers
                        ):
                            raise ProviderError("model_configs references an undeclared provider")
                        if not model.get("model") or model["resource_group"] not in concurrency.get(
                            "groups", {}
                        ):
                            raise ContractError(
                                "Explicit model and declared resource group required"
                            )
                    repeat = positive(group.get("repeat", 1))
                    for case, model, protocol in itertools.product(
                        resolved, group["model_configs"], group["protocols"]
                    ):
                        identifier(protocol)
                        settings = {
                            "model": model["model"],
                            "reasoning_effort": model.get("reasoning_effort"),
                            "max_rounds": group.get("max_audit_rounds"),
                            "timeout_seconds": group.get("timeout_seconds"),
                        }
                        provider = providers.get(model.get("provider", "openai"))
                        if provider is not None:
                            settings["provider"] = provider
                        slot = orchestration / "inputs" / f"input-{len(tasks) + 1:06d}"
                        spec = (
                            (source.parent / group["execution_spec_file"]).resolve()
                            if group.get("execution_spec_file")
                            else None
                        )
                        execution = backend.freeze(
                            case, protocol, project, slot, settings, spec_file=spec, codex=codex
                        )
                        for repetition in range(1, repeat + 1):
                            run_id = new_id("run")
                            tasks.append(
                                {
                                    "id": f"T{len(tasks) + 1:06d}",
                                    "batch_id": batch_id,
                                    "group": group["id"],
                                    "dependencies": dependencies[group["id"]],
                                    "repeat": repetition,
                                    "resource_group": model["resource_group"],
                                    "execution": execution,
                                    "run_id": run_id,
                                    "run_dir": str(directory / "runs" / run_id),
                                    "operation": "start",
                                    "operation_id": new_id("op"),
                                    "state": "QUEUED",
                                    "revision": 0,
                                    "auto_recoveries": 0,
                                    "evidence_validity": "VALID",
                                    "history": [],
                                }
                            )
            finally:
                registry.close()
            if not tasks:
                raise ContractError("Empty batch")
            plan = {
                "schema_version": 1,
                "id": batch_id,
                "request_id": request_id,
                "request_hash": request_hash,
                "name": data.get("name", batch_id),
                "created_at": now(),
                "concurrency": concurrency,
                "max_auto_recoveries": maximum,
                "tasks": tasks,
            }
            atomic(orchestration / "request.yaml", raw, raw=True)
            atomic(plan_path, plan)
        with store.transaction():
            store.db.execute(
                "INSERT INTO batches VALUES(?,?,?,?)",
                (batch_id, request_id, request_hash, json.dumps(plan)),
            )
            for task in plan["tasks"]:
                store.db.execute(
                    "INSERT INTO tasks VALUES(?,?,?,?,?)",
                    (task["id"], batch_id, "QUEUED", 0, json.dumps(task)),
                )
            store.event(
                batch_id, "submitted", {"request_id": request_id, "task_count": len(plan["tasks"])}
            )
        return plan
