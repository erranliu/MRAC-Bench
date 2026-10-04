import argparse
import sys
from pathlib import Path

from mrac_contracts.execution import ContractError
from mrac_contracts.providers import load_provider

from .cases import load_trial
from .codex_exec import CodexExecAdapter, saved_windows_sandbox
from .models import BenchError, RunConfig
from .protocol import DEFAULT_PROTOCOL_ID
from .providers import saved_provider
from .runner import run_case
from .workflows import saved_workflow


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "machine":
        from .machine import main as machine_main

        return machine_main(argv[1:])
    if (
        any(
            word in {"case", "batch", "orchestrator", "repo", "workspace", "stats"}
            for word in argv[:1]
        )
        or "--managed" in argv
        or "--bench-home" in argv
    ):
        from mrac_orchestrator.cli import main as managed_main

        return managed_main(argv)
    parser = argparse.ArgumentParser(
        prog="python -m mracbench",
        epilog="Managed commands: case, batch, orchestrator, repo, workspace; use <command> --help. "
        "Registered single runs: run --managed --case <number-or-name> --model <model>.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    run = common
    run.add_argument(
        "--protocol",
        dest="protocol_id",
        help=f"Protocol (run/trial default: {DEFAULT_PROTOCOL_ID})",
    )
    run.add_argument("--project-root", type=Path, default=Path.cwd())
    run.add_argument("--runs-dir", type=Path)
    run.add_argument("--workspace-dir", type=Path)
    run.add_argument("--spec-file", type=Path, help="Required input Spec for trial or exec-mrac")
    run.add_argument(
        "--model", help="Explicit model; otherwise Codex built-in default (user config ignored)"
    )
    run.add_argument(
        "--max-rounds", type=int, help="Override total audit cap (Spec flow default: 8)"
    )
    run.add_argument("--timeout", type=int, dest="timeout_seconds")
    run.add_argument(
        "--reasoning-effort",
        choices=("none", "minimal", "low", "medium", "high", "xhigh", "max"),
        help="Explicit model reasoning effort, saved for every stage and resume",
    )
    run.add_argument("--codex-executable", default="codex")
    run.add_argument("--windows-sandbox", choices=("elevated", "unelevated"), default="elevated")
    run.add_argument("--provider-file", type=Path, help="Explicit Responses provider YAML/JSON")
    run = sub.add_parser("run", parents=[common], help="Run one Spec-MRAC case")
    run.add_argument("--case", required=True, dest="case_id")
    trial = sub.add_parser("trial", parents=[common], help="Test a Spec before creating a case")
    trial.add_argument("--name", default="trial", dest="case_id", help="Label for run evidence")
    trial.add_argument("--repository-url", required=True)
    trial.add_argument("--commit", required=True, help="Full implementation-before commit SHA")
    trial.add_argument("--spec-path", default="SPEC.md", help="Spec path in the repair checkout")
    trial.add_argument("--related-spec", type=Path, action="append", default=[])
    resume = sub.add_parser(
        "resume", help="Resume a supported saved run without changing its protocol"
    )
    resume.add_argument("--run-dir", type=Path, required=True)
    resume.add_argument("--codex-executable", default="codex")
    resume.add_argument(
        "--input-file", type=Path, help="UTF-8 response for pending v2/exec questions"
    )
    resume.add_argument("--spec-file", type=Path, help="Import a revised working Spec for v2")
    for command in ("status", "report", "abort"):
        child = sub.add_parser(command, help=f"{command.title()} a v2/exec run")
        child.add_argument("--run-dir", type=Path, required=True)
        if command == "abort":
            child.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in {"resume", "abort"} and (args.run_dir / "owner.json").exists():
            import json

            owner = json.loads((args.run_dir / "owner.json").read_bytes())
            if owner.get("batch_id"):
                raise BenchError(
                    "RESUME_ERROR", "Batch-owned run: use batch recover/continue/answer/cancel"
                )
            if args.command == "resume":
                from mrac_orchestrator.cli import managed_resume

                return managed_resume(args.run_dir, args.input_file, args.spec_file)
        if args.command in {"status", "report", "abort"}:
            workflow = saved_workflow(args.run_dir)
            path, result = workflow.inspect(
                args.run_dir, abort_reason=args.reason if args.command == "abort" else None
            )
            if args.command == "report":
                report = workflow.report(path, result)
                if report:
                    print(report)
        elif args.command == "resume":
            provider = saved_provider(args.run_dir)
            adapter = CodexExecAdapter(
                args.codex_executable,
                provider=provider,
                windows_sandbox=saved_windows_sandbox(args.run_dir),
            )
            path, result = saved_workflow(args.run_dir).resume(
                args.run_dir, adapter, args.input_file, args.spec_file
            )
        else:
            input_case = None
            if args.command == "trial":
                args.protocol_id = args.protocol_id or DEFAULT_PROTOCOL_ID
                if args.spec_file is None:
                    raise BenchError("CASE_ERROR", "trial requires --spec-file")
                if args.protocol_id not in {"spec-flow-simple-v1", "spec-mrac-v2"}:
                    raise BenchError("CASE_ERROR", "trial supports only Spec audit/freeze flows")
                input_case = load_trial(
                    args.case_id,
                    args.spec_file,
                    args.repository_url,
                    args.commit,
                    args.spec_path,
                    args.related_spec,
                )
            provider = load_provider(args.provider_file) if args.provider_file else None
            adapter = CodexExecAdapter(
                args.codex_executable, provider=provider, windows_sandbox=args.windows_sandbox
            )
            project = args.project_root.resolve()
            config = RunConfig(
                project,
                args.case_id,
                (args.runs_dir or project / "runs").resolve(),
                (args.workspace_dir or project / ".workspaces").resolve(),
                args.model,
                args.max_rounds,
                args.timeout_seconds,
                args.protocol_id,
                args.reasoning_effort,
                args.spec_file if args.command == "run" else None,
                provider=provider,
            )
            path, result = run_case(config, adapter, input_case=input_case)
    except (BenchError, ContractError) as exc:
        print(f"{getattr(exc, 'kind', 'CONTRACT_ERROR')}: {exc}")
        return 2
    except OSError as exc:
        # An unwritable output root cannot hold even an error result.
        parser.exit(2, f"Cannot persist run: {exc}\n")
    print(f"Run: {result['run_id']}")
    print(f"Status: {result['status']}")
    if result["convergence_round"] is not None:
        print(f"Converged @ audit {result['convergence_round']}")
    if result["error"]:
        print(f"Error: {result['error']['message']}")
    if result.get("terminal_reason"):
        print(f"Reason: {result['terminal_reason']}")
    for question in result.get("flow", {}).get("questions", []):
        print(f"Required input: {question}")
    print(f"Result: {path / 'result.json'}")
    return {
        "CONVERGED": 0,
        "NON_CONVERGED": 1,
        "PAUSED": 3,
        "NEEDS_INPUT": 5,
        "ABORTED": 6,
        "RUNNING": 0,
    }.get(result["status"], 2)
