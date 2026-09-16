"""Dedicated writable checkouts and complete Git candidate patches for exec-mrac."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from .evidence import digest
from .models import BenchError
from .repository import git, tree_manifest


def git_bytes(path, *args, index=None):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_LITERAL_PATHSPECS": "1"}
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    try:
        process = subprocess.run(
            ["git", "-C", str(path), "-c", "gc.auto=0", *args],
            env=env,
            capture_output=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BenchError("REPOSITORY_ERROR", str(exc)) from exc
    if process.returncode:
        raise BenchError("REPOSITORY_ERROR", process.stderr.decode("utf-8", errors="replace"))
    return process.stdout


def checkout_path(root, run_path):
    root, run_path = root.resolve(), run_path.resolve()
    path = (root / "exec" / run_path.name).resolve()
    if (
        not path.is_relative_to(root)
        or path.is_relative_to(run_path)
        or run_path.is_relative_to(path)
    ):
        raise BenchError("REPOSITORY_ERROR", "Writable checkout and run evidence must be separate")
    return path


def prepare_exec_repository(case, root, run_path, *, create):
    path = checkout_path(root, run_path)
    log = run_path / "logs/repository.log"
    if create:
        path.mkdir(parents=True, exist_ok=False)
        args = ["init", "--quiet"]
        if len(case.commit) == 64:
            args.append("--object-format=sha256")
        git(path, *args, log=log)
        git(path, "remote", "add", "origin", case.repository_url, log=log)
        git(path, "fetch", "--depth=1", "--no-tags", "origin", case.commit, log=log)
        git(path, "-c", "advice.detachedHead=false", "checkout", "--detach", case.commit, log=log)
    if not (path / ".git").is_dir() or git(path, "rev-parse", "HEAD") != case.commit:
        raise BenchError("REPOSITORY_ERROR", "Execution checkout must retain the fixed HEAD")
    if git(path, "remote", "get-url", "origin") != case.repository_url:
        raise BenchError("REPOSITORY_ERROR", "Execution checkout origin changed")
    if any(line.startswith("160000 ") for line in git(path, "ls-files", "--stage").splitlines()):
        raise BenchError("REPOSITORY_ERROR", "Submodule execution is not supported")
    return ExecRepository(path, case.commit)


class ExecRepository:
    def __init__(self, path, commit):
        self.path, self.commit = path, commit
        self.control = self.control_state()
        self.expected_signature = None
        self.expected_workspace = None
        self.last_snapshot = None

    def control_state(self):
        state = {
            "head": git(self.path, "rev-parse", "HEAD"),
            "origin": git(self.path, "remote", "get-url", "origin"),
            "refs": git(self.path, "for-each-ref", "--format=%(refname) %(objectname)"),
            "index_tree": git_bytes(self.path, "write-tree").decode().strip(),
        }
        for name in ("config", "info/exclude", "info/attributes"):
            file = self.path / ".git" / name
            state[name] = digest(file.read_bytes()) if file.is_file() else None
        return state

    def snapshot(self, temporary_root):
        if not temporary_root.resolve().is_relative_to(temporary_root.parent.resolve()):
            raise BenchError("PROTOCOL_VIOLATION", "Candidate scratch directory escapes run")
        # Build an independent index: staged/unstaged modifications, deletions and all
        # normal untracked files enter one binary-capable patch without staging user files.
        with tempfile.TemporaryDirectory(prefix="candidate-index-", dir=temporary_root) as folder:
            index = Path(folder) / "index"
            git_bytes(self.path, "read-tree", self.commit, index=index)
            git_bytes(self.path, "add", "-A", "--", ".", index=index)
            tree = git_bytes(self.path, "write-tree", index=index).decode().strip()
            tracked = (
                git_bytes(self.path, "ls-tree", "-r", "--name-only", "-z", tree)
                .decode("utf-8")
                .split("\0")
            )
            patch = git_bytes(
                self.path,
                "diff",
                "--binary",
                "--full-index",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                self.commit,
                tree,
                "--",
            )
            names = (
                git_bytes(
                    self.path,
                    "diff",
                    "--name-status",
                    "--no-renames",
                    "-z",
                    self.commit,
                    tree,
                    "--",
                )
                .decode("utf-8")
                .split("\0")
            )
        changes = [{"status": names[i], "path": names[i + 1]} for i in range(0, len(names) - 1, 2)]
        ignored = (
            git_bytes(self.path, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
            .decode("utf-8")
            .split("\0")
        )
        # Raw-file hashes also cover ignored outputs: an auditor cannot mutate them
        # invisibly, and two clean rounds refer to an identical actual checkout.
        for file in self.path.rglob("*"):
            if file.relative_to(self.path).parts[0] == ".git":
                continue
            if not file.is_symlink() and not file.resolve().is_relative_to(self.path.resolve()):
                raise BenchError("PROTOCOL_VIOLATION", f"Checkout path escapes workspace: {file}")
        manifest = tree_manifest(self.path)
        product_files = {name: manifest[name] for name in tracked if name in manifest}
        signature = digest(
            json.dumps(
                {"base_head": self.commit, "tree": tree, "files": product_files},
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        )
        return {
            "base_head": self.commit,
            "tree": tree,
            "signature": signature,
            "workspace_sha256": digest(
                json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ),
            "patch_sha256": digest(patch),
            "patch": patch,
            "changes": changes,
            "working_files": manifest,
            "ignored_files": {name: manifest[name] for name in ignored if name in manifest},
        }

    def inspect(self):
        try:
            control = self.control_state()
            snapshot = self.snapshot(self.temporary_root)
            self.last_snapshot = snapshot
            return {
                "head": control["head"],
                "expected_head": self.commit,
                "control_changed": control != self.control,
                "candidate_sha256": snapshot["signature"],
                "tree": snapshot["tree"],
                "changes": snapshot["changes"],
                "violation": control != self.control
                or (
                    self.expected_signature is not None
                    and snapshot["signature"] != self.expected_signature
                )
                or (
                    self.expected_workspace is not None
                    and snapshot["workspace_sha256"] != self.expected_workspace
                ),
            }
        except (BenchError, OSError, UnicodeError) as exc:
            return {"violation": True, "inspection_error": str(exc)}

    def begin_invocation(self, *, readonly):
        # Validate the saved candidate before allowing a writer to change it.
        self.expected_workspace = self.last_snapshot["workspace_sha256"] if readonly else None
        if not readonly:
            self.expected_signature = None
