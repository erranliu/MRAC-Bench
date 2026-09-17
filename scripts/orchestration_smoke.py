"""Explicit, small real-model acceptance batch. No automatic budget extensions."""

import argparse
import subprocess
import time
from pathlib import Path

import yaml

from mrac_contracts.execution import OCCUPIED, atomic, digest, new_id
from mrac_orchestrator.backends.mrac import MRACBackend
from mrac_orchestrator.evidence import export_batch
from mrac_orchestrator.plan import submit
from mrac_orchestrator.scheduler import Scheduler
from mrac_orchestrator.store import Store
from mrac_resources.cases import Registry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bench-home", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--codex-executable", default="codex")
    parser.add_argument(
        "--protocol", choices=["both", "spec-mrac-v2", "exec-mrac-v1"], default="both"
    )
    parser.add_argument("--repeat", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    root = args.bench_home.resolve()
    label = new_id("smoke")
    source = root / "repos/fixtures" / label
    source.mkdir(parents=True)

    def git(*values):
        return subprocess.check_output(["git", "-C", str(source), *values], text=True).strip()

    git("init", "--quiet")
    git("config", "user.name", "MRAC smoke fixture")
    git("config", "user.email", "smoke@example.invalid")
    (source / "answer.py").write_text("def answer():\n    return 1\n", encoding="utf-8")
    git("add", "answer.py")
    git("commit", "--quiet", "-m", "Fixed smoke baseline")
    spec = (
        b"# Return value specification\n\n"
        b"## Required behavior\n"
        b"In answer.py, change the existing zero-argument function answer() to return "
        b"the integer 2 instead of 1. Preserve its public name, signature, and lack of side effects.\n\n"
        b"## Scope\n"
        b"Change only answer.py. No dependencies, configuration changes, documentation, "
        b"or new test files are required.\n\n"
        b"## Acceptance\n"
        b'From the checkout, run python -c "from answer import answer; assert answer() == 2". '
        b"A successful assertion and a diff limited to the return value satisfy this task.\n"
    )
    package = root / "control/smoke-inputs" / label
    package.mkdir(parents=True)
    (package / "spec.md").write_bytes(spec)
    case = {
        "id": label,
        "version": 1,
        "track": {"type": "spec"},
        "repository": {"url": source.as_uri(), "commit": git("rev-parse", "HEAD")},
        "task": {"file": "spec.md", "sha256": digest(spec)},
    }
    (package / "case.yaml").write_text(yaml.safe_dump(case), encoding="utf-8")
    registry = Registry(root)
    try:
        registered = registry.register(package, label, name=label)
    finally:
        registry.close()
    groups = []
    protocols = ("spec-mrac-v2", "exec-mrac-v1") if args.protocol == "both" else (args.protocol,)
    for protocol in protocols:
        group = {
            "id": protocol,
            "cases": [{"selector": registered["case_key"]}],
            "model_configs": [
                {
                    "model": args.model,
                    "reasoning_effort": args.reasoning_effort,
                    "resource_group": "smoke",
                }
            ],
            "protocols": [protocol],
            "repeat": args.repeat,
            "timeout_seconds": args.timeout,
        }
        if protocol == "exec-mrac-v1":
            group["execution_spec_file"] = str(package / "spec.md")
        else:
            group["max_audit_rounds"] = 2
        groups.append(group)
    request = root / "control/smoke-inputs" / f"{label}.yaml"
    request.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "name": label,
                "concurrency": {"total": 2, "groups": {"smoke": 2}},
                "retry": {"max_auto_recoveries": 0},
                "groups": groups,
            }
        ),
        encoding="utf-8",
    )
    store = Store(root)
    try:
        backend = MRACBackend(root)
        batch = submit(
            store,
            backend,
            request,
            Path(__file__).resolve().parents[1],
            label,
            args.codex_executable,
        )
        scheduler = Scheduler(store, backend, total=2)
        last = None
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            scheduler.tick()
            tasks = store.tasks(batch["id"])
            current = [(t["id"], t["state"], t.get("outcome")) for t in tasks]
            if current != last:
                print(current, flush=True)
                last = current
            if all(t["state"] not in OCCUPIED | {"QUEUED", "RETRY_WAIT"} for t in tasks):
                break
            time.sleep(1)
        report = export_batch(store, batch["id"])
        atomic(root / "smoke-result.json", report)
        print(root / "batches" / batch["id"] / "orchestration/report.md", flush=True)
        return 0 if report["state"] == "FINISHED" else 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
