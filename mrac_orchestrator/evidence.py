import json
from pathlib import Path

from mrac_contracts.execution import OCCUPIED, TERMINAL, atomic, digest, now
from mrac_resources.home import inventory
from mrac_resources.locks import file_lock
from mrac_resources.repositories import RepoPool


def summary(store, batch_id):
    tasks = store.tasks(batch_id)
    if any(t["state"] in OCCUPIED or t["state"] in {"QUEUED", "RETRY_WAIT"} for t in tasks):
        state = "RUNNING"
    elif any(t["state"] not in TERMINAL for t in tasks):
        state = "WAITING"
    elif all(t["state"] == "CANCELLED" for t in tasks):
        state = "CANCELLED"
    elif any(
        t["state"] in {"FAILED", "SKIPPED"} or t["evidence_validity"] != "VALID" for t in tasks
    ):
        state = "FINISHED_WITH_ERRORS"
    else:
        state = "FINISHED"
    return {"schema_version": 1, "batch_id": batch_id, "state": state, "tasks": tasks}


def export_batch(store, batch_id):
    directory = store.home / "batches" / batch_id / "orchestration"
    with file_lock(directory / ".export.lock"):
        task_rows = store.tasks(batch_id)
        paths = {r["run_dir"] for t in task_rows for r in [*t["history"], t]}
        for event in RepoPool(store.home).resource_events():
            resource = event["resource"]
            if resource.get("run_dir") not in paths and not paths.intersection(
                resource.get("users", [])
            ):
                continue
            operation_id = f"{event['id']}-{batch_id}"
            with store.transaction():
                if not store.db.execute(
                    "SELECT 1 FROM operations WHERE id=?", (operation_id,)
                ).fetchone():
                    store.event(batch_id, event["kind"], event)
                    store.db.execute(
                        "INSERT INTO operations VALUES(?,?,?)",
                        (operation_id, digest(event), json.dumps({"imported": True})),
                    )
        with store.transaction():
            data = summary(store, batch_id)
            events = [
                {
                    "event_id": f"{batch_id}:{row['seq']}",
                    "seq": row["seq"],
                    "at": row["at"],
                    **json.loads(row["data"]),
                }
                for row in store.db.execute(
                    "SELECT * FROM events WHERE batch_id=? ORDER BY seq", (batch_id,)
                )
            ]
            attempts = store.attempts(batch_id)
        high = events[-1]["seq"] if events else 0
        data.update(event_high_water=high, generated_at=now())
        # Full atomic regeneration makes truncated/missing exports recoverable from the DB.
        event_bytes = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events).encode()
        atomic(directory / "events.jsonl", event_bytes, raw=True)
        atomic(directory / "attempts.json", {"event_high_water": high, "attempts": attempts})
        atomic(directory / "summary.json", data)
        lines = [
            f"# Batch {batch_id}",
            "",
            f"State: {data['state']} · Event revision: {high}",
            "",
            "| Task | Case | Repeat | Model / Protocol | State / Outcome | Evidence |",
            "|---|---|---:|---|---|---|",
        ]
        for task in data["tasks"]:
            for row in [*task["history"], task]:
                execution = row["execution"]
                case, settings = execution["case"], execution["settings"]
                label = f"{case['case_key']} {case['name']} v{case['version']}".replace("|", "\\|")
                lines.append(
                    f"| {row['id']} | {label} | {row['repeat']} | {settings['model']} / {settings['protocol_id']} | {row['state']} / {row.get('outcome') or '—'} | [{row['run_id']}](../runs/{row['run_id']}/) {row['evidence_validity']} |"
                )
        lines += [
            "",
            "All attempts and previous experiments are retained in attempts.json and summary.json.",
            "Token/cost values not reported by the runner remain unknown. Active runs are incomplete.",
            "",
        ]
        atomic(directory / "report.md", "\n".join(lines).encode(), raw=True)
        manifest = {"schema_version": 1, "event_high_water": high, "files": {}}
        manifest["inputs"] = inventory(directory / "inputs")
        manifest["runs"] = {}
        for task in data["tasks"]:
            for row in [*task["history"], task]:
                if row["state"] in OCCUPIED:
                    manifest["runs"][row["run_id"]] = {"complete": False}
                else:
                    run_path = Path(row["run_dir"])
                    try:
                        with file_lock(run_path / ".execution.lock", blocking=False):
                            manifest["runs"][row["run_id"]] = {
                                "complete": (run_path / "seal.json").exists(),
                                "files": inventory(
                                    run_path,
                                    exclude=(
                                        ".execution.lock",
                                        ".repository-run.lock",
                                        ".run.lock",
                                    ),
                                ),
                            }
                    except OSError:
                        manifest["runs"][row["run_id"]] = {"complete": False}
        run_ids = {r["run_id"] for t in data["tasks"] for r in [*t["history"], t]}
        resource_records = [
            r
            for r in RepoPool(store.home).list()
            if r.get("run_id") in run_ids
            or any(Path(u).name in run_ids for u in r.get("users", []))
        ]
        atomic(directory / "repositories.json", resource_records)
        for name in (
            "request.yaml",
            "resolved-plan.json",
            "events.jsonl",
            "attempts.json",
            "summary.json",
            "report.md",
            "repositories.json",
        ):
            file = directory / name
            if file.exists():
                manifest["files"][name] = digest(file.read_bytes())
        atomic(directory / "manifest.json", manifest)
        if data["state"] not in {"RUNNING"}:
            for name in ("summary.json", "report.md", "manifest.json"):
                atomic(
                    directory / "revisions" / str(high) / name,
                    (directory / name).read_bytes(),
                    raw=True,
                )
        return data
