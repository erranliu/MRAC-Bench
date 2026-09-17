import argparse
import json
import sys
from pathlib import Path

from mrac_contracts.execution import ContractError, atomic, digest, new_id, read_json, utf8_stdio
from mrac_contracts.providers import load_provider
from mrac_resources.cases import Registry
from mrac_resources.home import home
from mrac_resources.locks import BusyError, file_lock
from mrac_resources.repositories import RepoPool

from .backends.mrac import MRACBackend
from .evidence import export_batch, summary
from .operations import operate
from .plan import positive, submit
from .scheduler import Scheduler
from .store import Store


def dump(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def online(root):
    try:
        with file_lock(root / "control/service.lock", blocking=False):
            return False
    except BusyError:
        return True


def parser():
    root = argparse.ArgumentParser(prog="mracbench")
    root.add_argument("--bench-home", type=Path)
    commands = root.add_subparsers(dest="command", required=True)
    case = commands.add_parser("case").add_subparsers(dest="action", required=True)
    register = case.add_parser("register")
    register.add_argument("source", type=Path)
    register.add_argument("--name")
    register.add_argument("--case", dest="selector")
    register.add_argument("--request-id", required=True)
    case.add_parser("list")
    for action in ("show", "rename", "archive", "unarchive"):
        child = case.add_parser(action)
        child.add_argument("selector")
        if action == "rename":
            child.add_argument("--name", required=True)
        if action == "show":
            child.add_argument("--case-version", type=int)
    batch = commands.add_parser("batch").add_subparsers(dest="action", required=True)
    child = batch.add_parser("submit")
    child.add_argument("source", type=Path)
    child.add_argument("--project-root", type=Path, default=Path.cwd())
    child.add_argument("--request-id", required=True)
    child.add_argument("--codex-executable", default="codex")
    for action in (
        "status",
        "report",
        "export",
        "recover",
        "continue",
        "answer",
        "rerun",
        "cancel",
    ):
        child = batch.add_parser(action)
        child.add_argument("batch_id")
        child.add_argument("--json", action="store_true")
        if action not in {"status", "report", "export"}:
            child.add_argument("--task", required=action != "cancel")
            child.add_argument("--operation-id", required=True)
            child.add_argument("--expected-revision", type=int)
        if action == "answer":
            child.add_argument("--input-file", type=Path, required=True)
    service = commands.add_parser("orchestrator").add_subparsers(dest="action", required=True)
    child = service.add_parser("serve")
    child.add_argument("--total", type=int, default=4)
    child.add_argument("--group", action="append", default=[], help="name=limit")
    service.add_parser("status")
    service.add_parser("stop")
    repo = commands.add_parser("repo").add_subparsers(dest="action", required=True)
    repo.add_parser("list")
    child = repo.add_parser("rebuild")
    child.add_argument("key")
    child = repo.add_parser("clean")
    child.add_argument("--apply", action="store_true")
    child.add_argument("--batch")
    child = (
        commands.add_parser("workspace")
        .add_subparsers(dest="action", required=True)
        .add_parser("release")
    )
    child.add_argument("--run-dir", type=Path, required=True)
    child.add_argument("--operation-id", required=True)
    child = commands.add_parser("run")
    child.add_argument("--managed", action="store_true", required=True)
    child.add_argument("--provider-file", type=Path)
    child.add_argument("--case", required=True)
    child.add_argument("--case-version", type=int)
    child.add_argument("--project-root", type=Path, default=Path.cwd())
    child.add_argument("--protocol", default="spec-mrac-v2")
    child.add_argument("--model", required=True)
    child.add_argument("--reasoning-effort")
    child.add_argument("--max-rounds", type=int)
    child.add_argument("--timeout", type=int)
    child.add_argument("--spec-file", type=Path)
    child.add_argument("--codex-executable", default="codex")
    return root


def main(argv=None):
    utf8_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    # Permit the shared root option before or after the command.
    if "--bench-home" in argv:
        index = argv.index("--bench-home")
        pair = argv[index : index + 2]
        del argv[index : index + 2]
        argv = pair + argv
    args = parser().parse_args(argv)
    root = home(args.bench_home)
    store = None
    registry = None
    try:
        if args.command == "case":
            registry = Registry(root)
            if args.action == "register":
                result = registry.register(
                    args.source, args.request_id, name=args.name, selector=args.selector
                )
            elif args.action == "list":
                result = registry.list()
            elif args.action == "show":
                result = registry.resolve(args.selector, args.case_version, archived=True)
            else:
                result = registry.update(
                    args.selector,
                    name=args.name if args.action == "rename" else None,
                    archived=None if args.action == "rename" else args.action == "archive",
                )
        elif args.command == "repo":
            pool = RepoPool(root)
            if args.action == "list":
                result = pool.list()
            elif args.action == "rebuild":
                result = pool.rebuild(args.key)
            else:
                result = pool.clean(apply=args.apply, batch_id=args.batch)
        elif args.command == "workspace":
            RepoPool(root).release(args.run_dir, args.operation_id)
            result = {"released": str(args.run_dir)}
        elif args.command == "run":
            return managed_run(root, args)
        else:
            store = Store(root)
            backend = MRACBackend(root)
            if args.command == "orchestrator":
                if args.action == "serve":
                    groups = dict(item.split("=", 1) for item in args.group)
                    Scheduler(
                        store,
                        backend,
                        total=positive(args.total),
                        groups={k: positive(int(v)) for k, v in groups.items()},
                    ).serve()
                    return 0
                if args.action == "stop":
                    atomic(root / "control/stop.json", {"stop": True})
                result = {"online": online(root)}
            elif args.action == "submit":
                batch = submit(
                    store,
                    backend,
                    args.source,
                    args.project_root,
                    args.request_id,
                    args.codex_executable,
                )
                result = {
                    "batch_id": batch["id"],
                    "tasks": len(batch["tasks"]),
                    "scheduler_online": online(root),
                }
                export_batch(store, batch["id"])
            elif args.action in {"status", "report", "export"}:
                store.batch(args.batch_id)
                result = (
                    summary(store, args.batch_id)
                    if args.action == "status"
                    else export_batch(store, args.batch_id)
                )
                result["scheduler_online"] = online(root)
                if args.action == "report" and not args.json:
                    print(
                        (root / "batches" / args.batch_id / "orchestration/report.md").read_text(
                            encoding="utf-8"
                        )
                    )
                    return 0
            else:
                tasks = [args.task] if args.task else [t["id"] for t in store.tasks(args.batch_id)]
                result = [
                    operate(
                        store,
                        backend,
                        args.batch_id,
                        task,
                        args.action,
                        args.operation_id if args.task else args.operation_id + "-" + task,
                        expected_revision=args.expected_revision,
                        input_file=getattr(args, "input_file", None),
                    )
                    for task in tasks
                ]
        dump(result)
        return 0
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 -- CLI error boundary
        dump({"error": str(exc) or "Interrupted"})
        return 2
    finally:
        if store:
            store.close()
        if registry:
            registry.close()


def managed_run(root, args):
    registry = Registry(root)
    try:
        case = registry.resolve(args.case, args.case_version)
    finally:
        registry.close()
    backend = MRACBackend(root)
    provider = load_provider(args.provider_file) if args.provider_file else None
    run_id = new_id("run")
    directory = root / "control/standalone" / run_id
    execution = backend.freeze(
        case,
        args.protocol,
        args.project_root,
        directory,
        {
            "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "max_rounds": args.max_rounds,
            "timeout_seconds": args.timeout,
            **({"provider": provider} if provider else {}),
        },
        spec_file=args.spec_file,
        codex=args.codex_executable,
    )
    task = {
        "run_id": run_id,
        "run_dir": str(root / "runs" / run_id),
        "execution": execution,
        "operation_id": new_id("op"),
        "operation": "start",
    }
    attempt = {"id": new_id("attempt"), "directory": str(directory / "worker")}
    request = backend.request(task, attempt)
    target = Path(attempt["directory"]) / "request.json"
    atomic(target, request)
    process = backend.launch(target)
    try:
        process.wait()
    except KeyboardInterrupt:
        atomic(target.parent / "cancel.json", {"cancel": True})
        process.wait(timeout=30)
    result = backend.observe(task, attempt)
    dump({"run_dir": task["run_dir"], **result})
    return 0 if result["state"] == "COMPLETED" else 2


def managed_resume(path, input_file=None, spec_file=None):
    path = Path(path).resolve()
    if spec_file:
        raise ContractError("A new managed Spec requires a new experiment")
    original = read_json(path / "managed-request.json")
    root = Path(original["bench_home"])
    backend = MRACBackend(root)
    public = backend.inspect(path)
    action = (
        "answer" if input_file else "continue" if public["lifecycle"] == "PAUSED" else "recover"
    )
    if action not in public.get("allowed_actions", []):
        raise ContractError("Run does not allow this resume operation")
    operation_id = new_id("op")
    task = {
        "run_id": original["run_id"],
        "run_dir": str(path),
        "execution": original,
        "operation": action,
        "operation_id": operation_id,
    }
    if input_file:
        content = Path(input_file).read_bytes()
        if not content.decode("utf-8-sig").strip():
            raise ContractError("Answer must be nonempty UTF-8")
        target = root / "control/standalone" / original["run_id"] / operation_id / "answer.md"
        atomic(target, content, raw=True)
        task.update(input_file=str(target), input_sha256=digest(content))
    attempt = {"id": new_id("attempt")}
    attempt["directory"] = str(root / "control/standalone" / original["run_id"] / attempt["id"])
    backend.reserve(task)
    request = backend.request(task, attempt)
    target = Path(attempt["directory"]) / "request.json"
    atomic(target, request)
    process = backend.launch(target)
    try:
        process.wait()
    except KeyboardInterrupt:
        atomic(target.parent / "cancel.json", {"cancel": True})
        process.wait(timeout=30)
    result = backend.observe(task, attempt)
    dump(result)
    return 0 if result["state"] == "COMPLETED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
