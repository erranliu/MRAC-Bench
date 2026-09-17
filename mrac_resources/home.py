import os
import shutil
from pathlib import Path

from mrac_contracts.execution import ContractError, digest


def home(value=None):
    return Path(value or os.environ.get("MRACBENCH_HOME", Path.home() / ".mracbench")).resolve()


def checked(root, path):
    root, path = Path(root).absolute(), Path(path).absolute()
    if not path.is_relative_to(root) or path == root:
        raise ContractError(f"Path outside managed root: {path}")
    for item in (root, *path.relative_to(root).parents):
        candidate = item if item.is_absolute() else root / item
        if candidate.is_symlink() or (
            hasattr(candidate, "is_junction") and candidate.is_junction()
        ):
            raise ContractError(f"Linked managed path: {candidate}")
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ContractError(f"Linked managed path: {path}")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ContractError(f"Resolved path escapes managed root: {path}")
    return path


def inventory(root, *, exclude=()):
    root = Path(root)
    files = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ".git" in relative.parts or relative.parts[0] in exclude:
            continue
        checked(root, path)
        if path.is_file():
            files[relative.as_posix()] = digest(path.read_bytes())
        elif not path.is_dir():
            raise ContractError(f"Unsupported package file: {path}")
    return files


def copy_package(source, target):
    files = inventory(source)
    Path(target).mkdir(parents=True, exist_ok=True)
    for name in files:
        destination = checked(target, Path(target) / name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(source) / name, destination)
    if inventory(target) != files:
        raise ContractError("Package changed while copying")
    return files
