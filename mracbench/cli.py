import argparse
from pathlib import Path

from .codex_exec import CodexExecAdapter
from .models import RunConfig
from .runner import run_case


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mracbench")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run one Spec-MRAC case")
    run.add_argument("--case", required=True, dest="case_id")
    run.add_argument("--project-root", type=Path, default=Path.cwd())
    run.add_argument("--runs-dir", type=Path)
    run.add_argument("--workspace-dir", type=Path)
    run.add_argument(
        "--model", help="Explicit model; otherwise Codex built-in default (user config ignored)"
    )
    run.add_argument("--max-rounds", type=int)
    run.add_argument("--timeout", type=int, dest="timeout_seconds")
    run.add_argument("--codex-executable", default="codex")
    args = parser.parse_args(argv)
    project = args.project_root.resolve()
    config = RunConfig(
        project,
        args.case_id,
        (args.runs_dir or project / "runs").resolve(),
        (args.workspace_dir or project / ".workspaces").resolve(),
        args.model,
        args.max_rounds,
        args.timeout_seconds,
    )
    try:
        path, result = run_case(config, CodexExecAdapter(args.codex_executable))
    except OSError as exc:
        # An unwritable output root cannot hold even an error result.
        parser.exit(2, f"Cannot persist run: {exc}\n")
    print(f"Run: {result['run_id']}")
    print(f"Status: {result['status']}")
    if result["convergence_round"] is not None:
        print(f"Converged @ audit {result['convergence_round']}")
    if result["error"]:
        print(f"Error: {result['error']['message']}")
    print(f"Result: {path / 'result.json'}")
    return 0 if result["status"] == "CONVERGED" else 1 if result["status"] == "NON_CONVERGED" else 2
