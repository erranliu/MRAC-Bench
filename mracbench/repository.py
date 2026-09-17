import hashlib
import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

from .models import BenchError, Case


def git(path: Path, *args: str, log: Path | None = None) -> str:
    command = ["git", "-C", str(path), *args]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=180,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"},
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BenchError("REPOSITORY_ERROR", f"Git invocation failed: {exc}") from exc
    if log is not None:
        with log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(command) + "\n" + result.stdout + result.stderr + "\n")
    if result.returncode:
        raise BenchError("REPOSITORY_ERROR", f"Git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def tree_manifest(workspace: Path) -> dict[str, str]:
    result = {}
    for path in workspace.rglob("*"):
        relative = path.relative_to(workspace)
        if relative.parts[0] == ".git":
            continue
        if path.is_symlink():
            result[relative.as_posix()] = "symlink:" + os.readlink(path)
        elif path.is_file():
            with path.open("rb") as stream:
                result[relative.as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


class Repository:
    def __init__(self, path: Path, commit: str, baseline: dict):
        self.path = path
        self.commit = commit
        self.baseline = baseline

    def inspect(self) -> dict:
        try:
            current = tree_manifest(self.path)
            head = git(self.path, "rev-parse", "HEAD")
            status = git(
                self.path, "status", "--porcelain=v1", "--untracked-files=all", "--ignored"
            )
            changes = {
                "head": head,
                "expected_head": self.commit,
                "git_status": status,
                "added": sorted(current.keys() - self.baseline.keys()),
                "removed": sorted(self.baseline.keys() - current.keys()),
                "modified": sorted(
                    k
                    for k in current.keys() & self.baseline.keys()
                    if current[k] != self.baseline[k]
                ),
            }
            changes["violation"] = bool(
                head != self.commit
                or status
                or changes["added"]
                or changes["removed"]
                or changes["modified"]
            )
            if changes["violation"]:
                changes["diff"] = git(self.path, "diff", "--binary", "HEAD", "--")
            return changes
        except (BenchError, OSError) as exc:
            # A removed/broken checkout after an agent invocation is itself a violation.
            return {"violation": True, "inspection_error": str(exc)}


@contextmanager
def prepare_repository(case: Case, root: Path, run_path: Path):
    root = root.resolve()
    if (root / ".managed.json").is_file():
        from mrac_contracts.execution import ContractError
        from mrac_resources.repositories import RepoPool

        try:
            with RepoPool(root.parent).readonly(case.repository_url, case.commit, run_path) as item:
                yield Repository(item[0], case.commit, item[1])
        except ContractError as exc:
            raise BenchError("REPOSITORY_ERROR", str(exc)) from exc
        return
    key = hashlib.sha256(case.repository_url.encode()).hexdigest()[:16] + "-" + case.commit
    path = (root / key[:16] / case.commit).resolve()
    if path.is_relative_to(run_path) or run_path.is_relative_to(path):
        raise BenchError(
            "REPOSITORY_ERROR", "Repository checkout and run directory must be separate"
        )
    if not path.is_relative_to(root):
        raise BenchError("REPOSITORY_ERROR", "Repository cache path escapes workspace root")
    locks = root / ".locks"
    locks.mkdir(parents=True, exist_ok=True)
    lock = locks / (key + ".lock")
    try:
        with lock.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps({"pid": os.getpid(), "run": str(run_path)}))
    except FileExistsError as exc:
        raise BenchError("REPOSITORY_ERROR", f"Checkout is locked: {lock}") from exc
    try:
        log = run_path / "logs" / "repository.log"
        if not path.exists():
            path.mkdir(parents=True)
            args = ["init", "--quiet"]
            if len(case.commit) == 64:
                args.append("--object-format=sha256")
            git(path, *args, log=log)
            git(path, "remote", "add", "origin", case.repository_url, log=log)
            # Only the selected snapshot: no later upstream fix or gold implementation.
            git(path, "fetch", "--depth=1", "--no-tags", "origin", case.commit, log=log)
            git(
                path,
                "-c",
                "advice.detachedHead=false",
                "checkout",
                "--detach",
                case.commit,
                log=log,
            )
        if not (path / ".git").is_dir():
            raise BenchError("REPOSITORY_ERROR", "Cache must be a standalone Git checkout")
        if git(path, "remote", "get-url", "origin", log=log) != case.repository_url:
            raise BenchError("REPOSITORY_ERROR", "Cached repository origin mismatch")
        if git(path, "rev-parse", "HEAD", log=log) != case.commit:
            raise BenchError("REPOSITORY_ERROR", "Checkout HEAD does not match fixed commit")
        if git(path, "status", "--porcelain=v1", "--untracked-files=all", "--ignored", log=log):
            raise BenchError(
                "REPOSITORY_ERROR", "Cached checkout is dirty; use a fresh workspace root"
            )
        if any(
            line.startswith("160000 ") for line in git(path, "ls-files", "--stage").splitlines()
        ):
            raise BenchError("REPOSITORY_ERROR", "Submodule repositories are not supported in M1")
        yield Repository(path, case.commit, tree_manifest(path))
    finally:
        lock.unlink(missing_ok=True)
