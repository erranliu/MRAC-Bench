import argparse
from pathlib import Path

from .codex_exec import CodexExecAdapter
from .models import BenchError, RunConfig
from .protocol import DEFAULT_PROTOCOL_ID
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
        "--model", help="Explicit model; otherwise Codex built-in default (user config ignored)"
    )
    run.add_argument("--max-rounds", type=int)
    run.add_argument("--timeout", type=int, dest="timeout_seconds")
    run.add_argument(
        "--reasoning-effort",
        choices=("none", "minimal", "low", "medium", "high", "xhigh", "max"),
        help="Explicit model reasoning effort, saved for every stage and resume",
    )
    run.add_argument("--codex-executable", default="codex")
    resume = sub.add_parser("resume", help="Continue an intact PAUSED spec-init-freeze run")
    resume.add_argument("--run-dir", type=Path, required=True)
    resume.add_argument("--codex-executable", default="codex")
    args = parser.parse_args(argv)
    try:
        adapter = CodexExecAdapter(args.codex_executable)
        if args.command == "resume":
            path, result = resume_run(args.run_dir, adapter)
        else:
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
    print(f"Result: {path / 'result.json'}")
    return {
        "CONVERGED": 0,
        "NON_CONVERGED": 1,
        "PAUSED": 3,
        "BLOCKED": 4,
    }.get(result["status"], 2)
