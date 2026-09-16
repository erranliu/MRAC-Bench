import argparse
from pathlib import Path

from .codex_exec import CodexExecAdapter
from .exec_flow import inspect_exec, resume_exec
from .models import BenchError, RunConfig
from .protocol import DEFAULT_PROTOCOL_ID
from .repository_flow import inspect_repository_run, resume_repository_run
from .runner import run_case
from .simple_flow import resume_run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mracbench")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run one Spec-MRAC case")
    run.add_argument("--case", required=True, dest="case_id")
    run.add_argument(
        "--protocol",
        dest="protocol_id",
        help=f"Protocol for this run (default: {DEFAULT_PROTOCOL_ID}; independent of case)",
    )
    run.add_argument("--project-root", type=Path, default=Path.cwd())
    run.add_argument("--runs-dir", type=Path)
    run.add_argument("--workspace-dir", type=Path)
    run.add_argument(
        "--spec-file", type=Path, help="Required immutable execution Spec for exec-mrac"
    )
    run.add_argument(
        "--model", help="Explicit model; otherwise Codex built-in default (user config ignored)"
    )
    run.add_argument("--max-rounds", type=int, help="Total audit cap; v2 has no default hard cap")
    run.add_argument("--timeout", type=int, dest="timeout_seconds")
    run.add_argument(
        "--reasoning-effort",
        choices=("none", "minimal", "low", "medium", "high", "xhigh", "max"),
        help="Explicit model reasoning effort, saved for every stage and resume",
    )
    run.add_argument("--codex-executable", default="codex")
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
        if args.command in {"status", "report", "abort"}:
            inspect = (
                inspect_exec
                if (args.run_dir / "exec-state.json").is_file()
                else inspect_repository_run
            )
            path, result = inspect(
                args.run_dir, abort_reason=args.reason if args.command == "abort" else None
            )
            if args.command == "report" and (path / "run-report.md").exists():
                print((path / "run-report.md").read_text(encoding="utf-8"))
        elif args.command == "resume":
            adapter = CodexExecAdapter(args.codex_executable)
            if (args.run_dir / "exec-state.json").is_file():
                if args.spec_file:
                    raise BenchError(
                        "RESUME_ERROR",
                        "Execution Spec is immutable; select a new Spec in a new run",
                    )
                path, result = resume_exec(args.run_dir, adapter, args.input_file)
            elif (args.run_dir / "repository-state.json").is_file():
                path, result = resume_repository_run(
                    args.run_dir, adapter, args.input_file, args.spec_file
                )
            else:
                if args.input_file or args.spec_file:
                    raise BenchError("RESUME_ERROR", "Input/Spec import is supported only by v2")
                path, result = resume_run(args.run_dir, adapter)
        else:
            adapter = CodexExecAdapter(args.codex_executable)
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
                args.spec_file,
            )
            path, result = run_case(config, adapter)
    except BenchError as exc:
        print(f"{exc.kind}: {exc}")
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
        "BLOCKED": 4,
        "NEEDS_INPUT": 5,
        "ABORTED": 6,
        "RUNNING": 0,
    }.get(result["status"], 2)
