"""Disposable MRAC-Bench candidates. PR interpretation stays with Codex."""

import argparse
import json
import math
import os
import re
import shutil
import stat
import sys
import uuid
from pathlib import Path

DEFAULT_MODELS = ["gpt-5.6-luna", "gpt-6.1-sol"]
DEFAULT_PROTOCOL = "spec-mrac-v2"
WINDOWS_SANDBOX = "unelevated"
PR_URL = re.compile(
    r"https://github\.com/([A-Za-z0-9][A-Za-z0-9-]*)/"
    r"([A-Za-z0-9][A-Za-z0-9_.-]*)/pull/([1-9][0-9]*)(?:/)?"
)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    roots = (Path.cwd(), *Path.cwd().parents, *Path(__file__).resolve().parents)
    default_root = next((root for root in roots if (root / "mracbench/cli.py").is_file()), None)
    default_root = os.environ.get("PR_CASE_BENCH_ROOT") or default_root
    parser.add_argument("--bench-root", type=Path, default=default_root)
    parser.add_argument("--bench-home", type=Path)
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("create")
    create.add_argument("--pr-url", required=True)
    sub.add_parser("list")
    test = sub.add_parser("test")
    test.add_argument("--candidate", required=True)
    test.add_argument("--commit")
    test.add_argument("--case-version", type=int)
    test.add_argument("--model", action="append")
    test.add_argument("--protocol")
    test.add_argument(
        "--reasoning-effort", choices=("none", "minimal", "low", "medium", "high", "xhigh", "max")
    )
    test.add_argument("--max-rounds", type=int)
    test.add_argument("--timeout", type=int)
    test.add_argument("--codex-executable", default="codex")
    save = sub.add_parser("save")
    save.add_argument("--candidate", required=True)
    save.add_argument("--accept", required=True, action="store_true")
    save.add_argument("--name")
    save.add_argument("--case", dest="selector")
    clean = sub.add_parser("clean")
    group = clean.add_mutually_exclusive_group(required=True)
    group.add_argument("--candidate")
    group.add_argument("--all", action="store_true")
    return parser.parse_args()


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2), flush=True)


def load_candidate(scratch, key):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", key) or key in {".", ".."}:
        raise ValueError("Invalid candidate ID")
    path = checked(scratch, scratch / key)
    data = json.loads((path / "candidate.json").read_bytes())
    if data.get("kind") != "pr-case" or data.get("candidate") != key:
        raise ValueError("Directory is not a pr-case candidate")
    case_id = data.get("case_id", "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", case_id) or case_id in {".", ".."}:
        raise ValueError("Invalid case ID")
    return path, data


def persist(path, data):
    atomic(path / "candidate.json", data)


def locations(path, data):
    package = checked(path, path / "project" / "cases" / data["case_id"])
    return {
        "path": str(path),
        "package": str(package),
        "spec_file": str(package / "spec.md"),
        "source": str(path / "source"),
    }


def idle(path):
    # Also covers an orphaned model process after a helper/terminal crash.
    for invocation in path.glob("runs/**/invocation.json"):
        checked(path, invocation)
        data = json.loads(invocation.read_bytes())
        if data.get("started") and not data.get("ended_at") and pid_alive(data.get("pid")):
            raise ValueError(f"Active model process: {data['pid']}; candidate retained")


def remove_candidate(scratch, path):
    # Check absolute targets and reject links before any recursive deletion.
    path = checked(scratch, path)
    idle(path)
    for child in path.rglob("*"):
        checked(path, child)

    def writable(function, filename, exc):
        target = Path(filename)
        if target != path:
            checked(path, target)
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        function(filename)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=writable)
    else:
        shutil.rmtree(path, onerror=writable)


def create_candidate(scratch, args):
    match = PR_URL.fullmatch(args.pr_url)
    if not match:
        raise ValueError("Expected https://github.com/owner/repo/pull/number")
    owner, repo, number = match.groups()
    case_id = f"{owner.lower()}__{repo.lower()}-{number}"
    key = f"{case_id}-{uuid.uuid4().hex[:8]}"
    path = checked(scratch, scratch / key)
    (path / "project" / "cases" / case_id).mkdir(parents=True)
    (path / "source").mkdir()
    data = {
        "kind": "pr-case",
        "candidate": key,
        "case_id": case_id,
        "pr_url": args.pr_url.rstrip("/"),
        "repository_url": f"https://github.com/{owner}/{repo}.git",
        "models": DEFAULT_MODELS.copy(),
        "protocol": DEFAULT_PROTOCOL,
        "windows_sandbox": WINDOWS_SANDBOX,
        "version": 1,
        "state": "DRAFT",
        "trials": [],
    }
    persist(path, data)
    return {**data, **locations(path, data)}


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def invocation_timings(run_path, result):
    durations = {"audit": [], "repair": []}
    for path in (run_path / "raw").glob("*/execution.json"):
        stage = path.parent.name
        role = (
            "audit"
            if stage.startswith(("audit-", "spec-init-", "spec-freeze-"))
            else "repair"
            if stage.startswith("repair-")
            else None
        )
        if role is None:
            continue
        checked(run_path, path)
        try:
            record = json.loads(path.read_bytes())
        except (OSError, ValueError):
            continue
        duration = record.get("duration_seconds")
        if record.get("started") is True and number(duration):
            durations[role].append(duration)
    return {
        role: {
            "calls": result.get(f"{role}_rounds"),
            "timed_calls": len(samples),
            "timed_total_seconds": round(sum(samples), 3) if samples else None,
            "average_seconds": round(sum(samples) / len(samples), 3) if samples else None,
        }
        for role, samples in durations.items()
    }


def basic_statistics(trials):
    statistics = {
        "run_count": len(trials),
        "converged_runs": sum(trial.get("status") == "CONVERGED" for trial in trials),
        "status_counts": {},
    }
    for trial in trials:
        status = trial.get("status", "UNKNOWN")
        statistics["status_counts"][status] = statistics["status_counts"].get(status, 0) + 1
    for field in ("wall_time_seconds", "audit_rounds", "repair_rounds"):
        samples = [trial[field] for trial in trials if number(trial.get(field))]
        statistics[f"{field}_samples"] = len(samples)
        statistics[f"average_{field}"] = round(sum(samples) / len(samples), 3) if samples else None
        if field == "wall_time_seconds":
            statistics["total_wall_time_seconds"] = (
                round(sum(samples), 3) if samples and len(samples) == len(trials) else None
            )
    statistics["timings"] = {}
    for role in ("audit", "repair"):
        records = [trial.get("timings", {}).get(role, {}) for trial in trials]
        calls = [record["calls"] for record in records if number(record.get("calls"))]
        measured = [
            record
            for record in records
            if number(record.get("timed_total_seconds")) and number(record.get("timed_calls"))
        ]
        count = sum(record["timed_calls"] for record in measured)
        total = sum(record["timed_total_seconds"] for record in measured)
        statistics["timings"][role] = {
            "calls": sum(calls) if len(calls) == len(trials) and calls else None,
            "timed_calls": count,
            "timed_total_seconds": round(total, 3) if count else None,
            "average_seconds": round(total / count, 3) if count else None,
        }
    return statistics


def trial_summary(model, run_path, result, content, workflow):
    artifact = result.get("final_artifact")
    final = None
    if artifact:
        artifact_path = Path(artifact)
        if not artifact_path.is_absolute():
            artifact_path = run_path / artifact_path
        artifact_path = checked(run_path, artifact_path)
        if artifact_path.is_file():
            final = artifact_path
    return {
        "model": model,
        "run_id": result["run_id"],
        "status": result["status"],
        "protocol": result.get("protocol_id"),
        "protocol_version": result.get("protocol_version"),
        "audit_rounds": result.get("audit_rounds"),
        "repair_rounds": result.get("repair_rounds"),
        "convergence_round": result.get("convergence_round"),
        "wall_time_seconds": (result.get("usage") or {}).get("wall_time_seconds"),
        "timings": invocation_timings(run_path, result),
        "run_dir": str(run_path),
        "result_file": str(run_path / "result.json"),
        "final_artifact": str(final) if final else None,
        "artifact_type": "code_patch" if workflow == "exec-mrac" else "spec",
        "spec_changed": final.read_bytes() != content
        if final and workflow != "exec-mrac"
        else None,
        "terminal_reason": result.get("terminal_reason"),
        "questions": (result.get("flow") or {}).get("questions", []),
        "error": result.get("error"),
    }


def test_candidate(scratch, args):
    import yaml

    from mracbench.cases import load_case
    from mracbench.codex_exec import CodexExecAdapter
    from mracbench.models import RunConfig
    from mracbench.protocol import load_protocol
    from mracbench.runner import run_case

    path, data = load_candidate(scratch, args.candidate)
    idle(path)
    commit = args.commit or data.get("commit", "")
    if not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", commit):
        raise ValueError("First test requires --commit with a full implementation-before SHA")
    version = args.case_version if args.case_version is not None else data["version"]
    if version < 1:
        raise ValueError("case-version must be positive")
    models = args.model if args.model is not None else data["models"]
    if any(not model.strip() or model.startswith("-") for model in models):
        raise ValueError("Invalid model name")
    protocol_id = args.protocol or data["protocol"]
    protocol = load_protocol(args.bench_root, protocol_id)
    for key, value in (("max_rounds", args.max_rounds), ("timeout", args.timeout)):
        if value is not None and value < 1:
            raise ValueError(f"{key} must be positive")
    package = Path(locations(path, data)["package"])
    spec = checked(package, package / "spec.md")
    content = spec.read_bytes()
    if not content.decode("utf-8-sig").strip():
        raise ValueError("spec.md must be nonempty UTF-8")
    case = {
        "id": data["case_id"],
        "version": version,
        "source": {"upstream_url": data["pr_url"]},
        "repository": {"url": data["repository_url"], "commit": commit.lower()},
        "task": {"file": "spec.md", "sha256": digest(content)},
        "track": {"type": "spec"},
    }
    atomic(package / "case.yaml", yaml.safe_dump(case, sort_keys=False).encode("utf-8"), raw=True)
    load_case(path / "project", data["case_id"])
    target_protocol = checked(path, path / "project" / "protocols" / protocol_id)
    copy_package(args.bench_root / "protocols" / protocol_id, target_protocol)
    data.update(
        commit=commit.lower(),
        version=version,
        models=models,
        protocol=protocol_id,
        windows_sandbox=WINDOWS_SANDBOX,
    )
    for key in ("reasoning_effort", "max_rounds", "timeout"):
        value = getattr(args, key)
        if value is not None:
            data[key] = value
    data.update(state="TESTING", trials=[], tested_package=inventory(package))
    data.pop("statistics", None)
    persist(path, data)
    for index, model in enumerate(models, 1):
        emit({"testing": model, "invocation": index, "total": len(models)})
        config = RunConfig(
            project_root=path / "project",
            case_id=data["case_id"],
            runs_dir=path / "runs",
            workspace_dir=path / "workspaces" / uuid.uuid4().hex[:12],
            model=model,
            protocol_id=protocol_id,
            reasoning_effort=data.get("reasoning_effort"),
            max_rounds=data.get("max_rounds"),
            timeout_seconds=data.get("timeout"),
            spec_file=spec if protocol.workflow == "exec-mrac" else None,
        )
        try:
            adapter = CodexExecAdapter(args.codex_executable, windows_sandbox=WINDOWS_SANDBOX)
            run_path, result = run_case(config, adapter)
            trial = trial_summary(model, run_path, result, content, protocol.workflow)
        except Exception as exc:  # noqa: BLE001 -- record a failed trial and try the next model
            trial = {"model": model, "status": "ERROR", "error": str(exc)}
        trial["windows_sandbox"] = WINDOWS_SANDBOX
        data["trials"].append(trial)
        persist(path, data)
        emit(trial)
    data["state"] = "REVIEW_PENDING"
    data["statistics"] = {
        "overall": basic_statistics(data["trials"]),
        "by_model": {
            model: basic_statistics([trial for trial in data["trials"] if trial["model"] == model])
            for model in models
        },
    }
    persist(path, data)
    return {**data, **locations(path, data)}


def save_candidate(scratch, args, bench_home):
    from mrac_resources.cases import Registry

    path, data = load_candidate(scratch, args.candidate)
    idle(path)
    package = Path(locations(path, data)["package"])
    if data.get("state") not in {"REVIEW_PENDING", "REGISTERED"} or not data["trials"]:
        raise ValueError("Run the candidate and obtain human acceptance before saving")
    files = inventory(package)
    if set(files) != {"case.yaml", "spec.md"} or files != data.get("tested_package"):
        raise ValueError("Candidate input changed since the last test; retest before saving")
    registration = data.setdefault(
        "registration",
        {"request_id": f"pr-case-{uuid.uuid4().hex}", "name": args.name, "selector": args.selector},
    )
    if args.name is not None and args.name != registration["name"]:
        raise ValueError("Retry registration with the original name")
    if args.selector is not None and args.selector != registration["selector"]:
        raise ValueError("Retry registration with the original case selector")
    persist(path, data)
    registry = Registry(bench_home)
    try:
        registered = registry.register(package, **registration)
        permanent = registry.resolve(registered["number"], registered["version"])
    finally:
        registry.close()
    data["state"] = "REGISTERED"
    persist(path, data)
    result = {
        "case_key": permanent["case_key"],
        "version": permanent["version"],
        "path": permanent["path"],
    }
    try:
        remove_candidate(scratch, path)
        result["cleaned"] = str(path)
    except (OSError, ValueError) as exc:
        result["cleanup_error"] = str(exc)
        result["candidate_retained"] = str(path)
    return result


def clean_candidates(scratch, args):
    keys = (
        [args.candidate]
        if args.candidate
        else [
            item.name
            for item in sorted(scratch.iterdir())
            if item.is_dir() and (item / "candidate.json").is_file()
        ]
    )
    result = {"cleaned": [], "retained": []}
    for key in keys:
        try:
            path, _ = load_candidate(scratch, key)
            remove_candidate(scratch, path)
            result["cleaned"].append(str(path))
        except (OSError, ValueError) as exc:
            result["retained"].append({"candidate": key, "reason": str(exc)})
    return result


def main():
    args = arguments()
    if args.bench_root is None:
        raise ValueError("Set --bench-root or PR_CASE_BENCH_ROOT to an MRAC-Bench checkout")
    args.bench_root = args.bench_root.resolve()
    if not (args.bench_root / "mracbench" / "cli.py").is_file():
        raise ValueError("Set --bench-root to an existing MRAC-Bench checkout")
    sys.path.insert(0, str(args.bench_root))
    global atomic, digest, checked, copy_package, inventory, pid_alive
    from mrac_contracts.execution import atomic, digest, utf8_stdio
    from mrac_resources.home import checked, copy_package, home, inventory
    from mrac_resources.locks import file_lock
    from mracbench.session import pid_alive

    utf8_stdio()
    bench_home = home(args.bench_home)
    scratch = checked(bench_home, bench_home / "pr-case-scratch")
    scratch.mkdir(parents=True, exist_ok=True)
    # A single scratch lock keeps run/save/clean mutually exclusive, including on Windows.
    with file_lock(scratch / ".activity.lock", blocking=False):
        if args.action == "create":
            result = create_candidate(scratch, args)
        elif args.action == "test":
            result = test_candidate(scratch, args)
        elif args.action == "save":
            result = save_candidate(scratch, args, bench_home)
        elif args.action == "clean":
            result = clean_candidates(scratch, args)
        else:
            result = []
            for item in sorted(scratch.iterdir()):
                if item.is_dir() and (item / "candidate.json").is_file():
                    path, data = load_candidate(scratch, item.name)
                    result.append({**data, **locations(path, data)})
        emit(result)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 -- report helper failures without deleting the candidate
        emit({"error": str(exc)})
        sys.exit(2)
