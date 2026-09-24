"""Disposable repository checkouts for file-based Spec repairs."""

from pathlib import Path, PurePosixPath

from .cases import load_yaml
from .models import BenchError
from .repository import git, tree_manifest


def spec_path(case):
    track = load_yaml(case.snapshots["case.yaml"], "case.yaml")["track"]
    name = track.get("path", "SPEC.md")
    if (
        not isinstance(name, str)
        or not name
        or "\\" in name
        or ":" in name
        or PurePosixPath(name).is_absolute()
        or any(part in {"", ".", "..", ".git"} for part in name.split("/"))
    ):
        raise BenchError("CASE_ERROR", "track.path must be a safe repository-relative Spec path")
    return name


class SpecCheckout:
    def __init__(self, repo, path: Path, spec_name: str, seed: bytes):
        self.repo = repo
        self.path = path
        self.spec_name = spec_name
        if (
            path.is_symlink()
            or (hasattr(path, "is_junction") and path.is_junction())
            or (path.exists() and not path.is_dir())
        ):
            raise BenchError("PROTOCOL_VIOLATION", "Repair checkout is not a regular directory")
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            git(
                repo.path,
                "clone",
                "--no-local",
                "--quiet",
                "--no-checkout",
                str(repo.path),
                str(path),
            )
            git(path, "checkout", "--quiet", "--detach", repo.commit)
            git(path, "remote", "remove", "origin")
        if git(path, "rev-parse", "HEAD") != repo.commit:
            raise BenchError("PROTOCOL_VIOLATION", "Repair checkout HEAD changed")
        target = path / spec_name
        if any(
            name.casefold() == spec_name.casefold() and name != spec_name for name in repo.baseline
        ):
            raise BenchError("CASE_ERROR", f"Spec path differs only by case: {spec_name}")
        # Separate clean checkouts at the same commit can have different working
        # bytes when their Git checkout filters/configuration differ. Compare this
        # checkout to its own HEAD, then snapshot its bytes for post-call checks.
        status = git(
            path,
            "-c",
            "core.quotePath=false",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignored",
            strip=False,
        )
        if any(
            len(item) < 4
            or item[2] != " "
            or item[3:] != spec_name
            or any(mark in item[:2] for mark in "RCUD")
            for item in status.split("\x00")
            if item
        ):
            raise BenchError("PROTOCOL_VIOLATION", "Repair checkout changed outside the Spec")
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise BenchError("CASE_ERROR", f"Spec path is not a regular file: {spec_name}")
        parents = target.relative_to(path).parts[:-1]
        cursor = path
        for part in parents:
            cursor = cursor / part
            if cursor.is_symlink() or (hasattr(cursor, "is_junction") and cursor.is_junction()):
                raise BenchError("CASE_ERROR", "Spec path crosses a repository link")
        if not target.parent.resolve().is_relative_to(path.resolve()):
            raise BenchError("CASE_ERROR", "Spec path crosses a repository symlink")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(seed)
        self.baseline = tree_manifest(path)

    @property
    def target(self):
        return self.path / self.spec_name

    def candidate(self):
        if git(self.path, "rev-parse", "HEAD") != self.repo.commit:
            raise BenchError("PROTOCOL_VIOLATION", "Repair checkout HEAD changed")
        if self.target.is_symlink() or not self.target.is_file():
            raise BenchError("PROTOCOL_VIOLATION", "Spec was removed or replaced with a link")
        current = tree_manifest(self.path)
        if current.keys() != self.baseline.keys() or any(
            current[name] != expected
            for name, expected in self.baseline.items()
            if name != self.spec_name
        ):
            raise BenchError("PROTOCOL_VIOLATION", "Repair changed files outside the Spec")
        try:
            return self.target.read_bytes()
        except OSError as exc:
            raise BenchError("PROTOCOL_VIOLATION", f"Cannot read repaired Spec: {exc}") from exc
