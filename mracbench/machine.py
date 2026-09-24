"""Public process contract. Private checkpoints are interpreted only inside the runner."""

import argparse
import json
import sys
from pathlib import Path

from mrac_contracts.execution import (
    ContractError,
    atomic,
    digest,
    now,
    outcome,
    read_json,
    utf8_stdio,
    validate_request,
)
from mrac_contracts.providers import normalize_provider, validate_selection
from mrac_resources.home import inventory
from mrac_resources.locks import BusyError, file_lock
from mrac_resources.repositories import RepoPool

from .cases import load_case, positive_int
from .codex_exec import CodexExecAdapter
from .models import BenchError, RunConfig
from .protocol import load_protocol


def code_identity():
    root = Path(__file__).resolve().parent.parent
    files = {}
    for package in ("mracbench", "mrac_contracts", "mrac_resources", "mrac_orchestrator"):
        for path in sorted((root / package).rglob("*.py")):
            files[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    return {"sha256": digest(files), "python": sys.version, "executable": sys.executable}


def seal(path):
    files = inventory(
        path,
        exclude=(
            ".execution.lock",
            ".repository-run.lock",
            ".run.lock",
            "checkout",
            "seal.json",
            "lifecycle.json",
        ),
    )
    files = {
        k: v
        for k, v in files.items()
        if k not in {"seal.json", "lifecycle.json"}
        and not k.endswith((".lock", ".tmp"))
        and not k.startswith(".operations/")
    }
    atomic(path / "seal.json", {"schema_version": 1, "files": files, "at": now()})


def validate(bundle, settings):
    bundle = Path(bundle)
    provider = normalize_provider(settings.get("provider"))
    validate_selection(provider, settings.get("model"), settings.get("reasoning_effort"))
    if provider is not None:
        settings = {**settings, "provider": provider}
    case = load_case(bundle, settings["case_id"])
    protocol = load_protocol(bundle, settings["protocol_id"])
    maximum = settings.get("max_rounds")
    if protocol.workflow == "exec-mrac":
        if maximum is not None and maximum != 6:
            raise ContractError("exec-mrac requires six-audit batches")
        maximum = 6
        spec = bundle / "execution-spec.md"
        if not spec.read_text(encoding="utf-8-sig").strip():
            raise ContractError("Execution Spec is required")
    elif (bundle / "execution-spec.md").exists():
        raise ContractError("Execution Spec supplied for a non-exec protocol")
    elif maximum is None:
        maximum = (
            protocol.max_audit_rounds
            if protocol.workflow == "repository-spec-freeze"
            else case.max_audit_rounds or protocol.max_audit_rounds
        )
    if maximum is not None:
        positive_int(maximum, "max_rounds")
    timeout_value = settings.get("timeout_seconds")
    timeout = positive_int(
        case.timeout_seconds if timeout_value is None else timeout_value, "timeout"
    )
    return {
        **settings,
        "max_rounds": maximum,
        "timeout_seconds": timeout,
        "protocol_version": protocol.version,
        "repository_access": (
            "writable"
            if protocol.workflow == "exec-mrac"
            or (protocol.workflow == "repository-spec-freeze" and protocol.version >= 3)
            or (protocol.workflow == "spec-init-freeze" and protocol.version >= 10)
            else "read_only"
        ),
    }


def inspect(path):
    path = Path(path).resolve()
    public = read_json(path / "lifecycle.json")
    if public.get("schema_version") != 1 or public.get("run_id") != path.name:
        raise ContractError("Unsupported or mismatched public run status")
    try:
        with file_lock(path / ".execution.lock", blocking=False):
            pass
    except BusyError:
        return public
    if public.get("infrastructure_terminal"):
        return public
    try:
        if public["lifecycle"] != "RUNNING":
            seal_file = path / "seal.json"
            if public["lifecycle"] == "COMPLETED" and not seal_file.exists():
                raise ContractError("Completed run evidence has not been sealed")
            if seal_file.exists():
                for name, expected in read_json(seal_file)["files"].items():
                    if digest((path / name).read_bytes()) != expected:
                        raise ContractError(f"Sealed run evidence changed: {name}")
        if (path / "exec-state.json").exists():
            from .exec_flow import load_exec, verify_checkout

            store, result, case, _, config = load_exec(path)
            if result["flow"].get("candidate") is not None:
                verify_checkout(store, result, case, config)
            else:
                raise ContractError("No verified writable candidate checkpoint")
            actions = ["recover"] if result["status"] not in {"CONVERGED", "ABORTED"} else []
            if result["status"] == "PAUSED":
                actions = ["continue"]
            if result["status"] == "NEEDS_INPUT":
                actions = ["answer"]
        elif (path / "repository-state.json").exists():
            from .repository_flow import load_session

            _, result, _, _, _, _ = load_session(path)
            actions = (
                ["recover"]
                if result["status"] not in {"CONVERGED", "NON_CONVERGED", "ABORTED"}
                else []
            )
            if result["status"] == "PAUSED":
                actions = ["continue"]
            if result["status"] == "NEEDS_INPUT":
                actions = ["answer"]
        else:
            result = read_json(path / "result.json")
            actions = (
                ["continue"]
                if result.get("protocol_id") == "spec-flow-simple-v1"
                and result.get("flow", {}).get("schema_version") == 3
                and result.get("protocol_version") in {4, 5, 6, 7, 8, 9, 10}
                and result["status"] == "PAUSED"
                else []
            )
        artifact = result.get("final_artifact")
        artifacts = []
        if artifact and (path / artifact).is_file():
            artifacts.append({"path": artifact, "sha256": digest((path / artifact).read_bytes())})
        return {
            **public,
            "runner_status": result["status"],
            "outcome": result["status"],
            "lifecycle": "FAILED"
            if public.get("runner_status") == "EXECUTION_ERROR"
            else outcome(result["status"]),
            "checkpoint_valid": True,
            "allowed_actions": actions,
            "artifacts": artifacts,
            "result": result,
        }
    except (OSError, ValueError, KeyError, TypeError, BenchError) as exc:
        return {**public, "checkpoint_valid": False, "allowed_actions": [], "error": str(exc)}


def execute(request, adapter=None):
    request = validate_request(request)
    run_dir, bundle = Path(request["run_dir"]), Path(request["bundle"])
    if inventory(bundle) != request["bundle_manifest"]:
        raise ContractError("Frozen input bundle changed")
    if code_identity() != request["code_identity"]:
        raise ContractError("Runner execution environment changed")
    provider = normalize_provider(request["settings"].get("provider"))
    if provider is not None and read_json(bundle / "provider.json") != provider:
        raise ContractError("Provider settings differ from the frozen bundle")
    adapter = adapter or CodexExecAdapter(
        request.get("codex_executable", "codex"), provider=provider
    )
    if adapter.version() != request["agent_version"]:
        raise ContractError("Agent executable version changed")
    run_dir.mkdir(parents=True, exist_ok=True)
    current = inspect(run_dir) if (run_dir / "lifecycle.json").exists() else {}
    with file_lock(run_dir / ".execution.lock", blocking=False):
        owner_file = run_dir / "owner.json"
        if owner_file.exists():
            owner = read_json(owner_file)
            if owner["run_spec_sha256"] != request["run_spec_sha256"]:
                raise ContractError("run_id cannot be reused for different input")
        else:
            atomic(
                owner_file,
                {
                    "run_spec_sha256": request["run_spec_sha256"],
                    "batch_id": request.get("batch_id"),
                    "run_id": request["run_id"],
                    "bench_home": request["bench_home"],
                },
            )
        operation = run_dir / ".operations" / (request["operation_id"] + ".json")
        if operation.exists():
            old = read_json(operation)
            if old["request_sha256"] != request["request_sha256"]:
                raise ContractError("Operation reused with different request")
            return current or read_json(run_dir / "lifecycle.json")
        if request["operation"] != "start" and request["operation"] not in current.get(
            "allowed_actions", []
        ):
            raise ContractError("Operation not allowed by the current checkpoint")
        previous = (
            read_json(run_dir / "lifecycle.json") if (run_dir / "lifecycle.json").exists() else {}
        )
        if request["operation"] != "start" and previous.get("revision") != request.get(
            "expected_revision"
        ):
            raise ContractError("Run revision conflict")
        if request["operation"] == "start" and (run_dir / "run.yaml").exists():
            raise ContractError("Already initialized; inspect and recover this run")
        pool = RepoPool(request["bench_home"])
        pool.record_run(request["run_id"], run_dir, request.get("batch_id") or "standalone")
        if not (run_dir / "managed-request.json").exists():
            atomic(run_dir / "managed-request.json", request)
        status = {
            "schema_version": 1,
            "run_id": request["run_id"],
            "attempt_id": request["attempt_id"],
            "revision": previous.get("revision", 0) + 1,
            "request_sha256": request["request_sha256"],
            "lifecycle": "RUNNING",
            "runner_status": "RUNNING",
            "allowed_actions": [],
            "checkpoint_valid": False,
            "updated_at": now(),
        }
        atomic(operation, {"request_sha256": request["request_sha256"], "started_at": now()})
        atomic(run_dir / "lifecycle.json", status)
        settings = request["settings"]
        try:
            if request["operation"] == "start":
                from .runner import run_case

                config = RunConfig(
                    bundle,
                    settings["case_id"],
                    run_dir.parent,
                    pool.root,
                    settings["model"],
                    settings.get("max_rounds"),
                    settings["timeout_seconds"],
                    settings["protocol_id"],
                    settings.get("reasoning_effort"),
                    bundle / "execution-spec.md"
                    if (bundle / "execution-spec.md").exists()
                    else None,
                    request["run_id"],
                    provider=provider,
                )
                _, result = run_case(config, adapter)
            else:
                response = Path(request["input_file"]) if request.get("input_file") else None
                if response and digest(response.read_bytes()) != request["input_sha256"]:
                    raise ContractError("Answer changed")
                if (run_dir / "exec-state.json").exists():
                    from .exec_flow import resume_exec

                    _, result = resume_exec(
                        run_dir,
                        adapter,
                        response,
                        recover=request["operation"] in {"recover", "answer"},
                    )
                elif (run_dir / "repository-state.json").exists():
                    from .repository_flow import resume_repository_run

                    _, result = resume_repository_run(run_dir, adapter, response)
                elif request["operation"] == "continue":
                    from .simple_flow import resume_run

                    _, result = resume_run(run_dir, adapter)
                else:
                    raise ContractError("RECOVERY_UNSUPPORTED")
            status.update(
                lifecycle=outcome(result["status"]),
                runner_status=result["status"],
                outcome=result["status"],
                result=result,
                checkpoint_valid=True,
            )
        except Exception as exc:  # noqa: BLE001 -- persist unexpected runner failures
            status.update(lifecycle="FAILED", error=str(exc), runner_status="EXECUTION_ERROR")
        status.update(updated_at=now(), revision=status["revision"] + 1)
        atomic(run_dir / "lifecycle.json", status)
        atomic(
            operation,
            {"request_sha256": request["request_sha256"], "finished_at": now(), "status": status},
        )
        seal(run_dir)
        return status


def main(argv=None):
    utf8_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=["capabilities", "validate", "start", "resume", "inspect", "abort"]
    )
    parser.add_argument("--request", type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--codex-executable", default="codex")
    parser.add_argument("--operation-id")
    parser.add_argument("--expected-revision", type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "capabilities":
            result = {
                "contract_version": 1,
                "code_identity": code_identity(),
                "agent_version": CodexExecAdapter(args.codex_executable).version(),
                "protocols": {
                    "spec-mrac-v2": 3,
                    "exec-mrac-v1": 1,
                    "spec-mrac-v1": 1,
                    "spec-flow-simple-v1": 10,
                },
            }
        elif args.command == "inspect":
            result = inspect(args.run_dir)
        elif args.command == "validate":
            request = read_json(args.request)
            result = validate(request["bundle"], request["settings"])
        elif args.command == "abort":
            with file_lock(args.run_dir / ".execution.lock", blocking=False):
                result = read_json(args.run_dir / "lifecycle.json")
                if result["lifecycle"] != "CANCELLED":
                    if not args.operation_id or args.expected_revision != result["revision"]:
                        raise ContractError("Abort requires operation_id and current revision")
                    result.update(
                        lifecycle="CANCELLED",
                        cancel_operation_id=args.operation_id,
                        infrastructure_terminal=True,
                        allowed_actions=[],
                        revision=result["revision"] + 1,
                    )
                    atomic(args.run_dir / "lifecycle.json", result)
                    seal(args.run_dir)
        else:
            result = execute(read_json(args.request))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001 -- machine-readable error boundary
        print(json.dumps({"error": str(exc), "schema_version": 1}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
