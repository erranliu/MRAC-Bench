import os
import shutil
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path

from mrac_contracts.execution import ContractError, atomic, digest, new_id, now, read_json

from .home import checked, home
from .locks import BusyError, file_lock


def git(path, *args):
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        timeout=180,
        check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"},
    )
    if result.returncode:
        raise ContractError(result.stderr.decode("utf-8", errors="replace"))
    return result.stdout.decode("utf-8", errors="strict").strip()


def fingerprint(path):
    files = {}
    for item in sorted(Path(path).rglob("*")):
        relative = item.relative_to(path)
        if relative.parts[0] == ".git":
            continue
        if item.is_symlink():
            files[relative.as_posix()] = "symlink:" + os.readlink(item)
        elif item.is_file():
            checked(path, item)
            files[relative.as_posix()] = digest(item.read_bytes())
    return {
        "files": files,
        "head": git(path, "rev-parse", "HEAD"),
        "status": git(path, "status", "--porcelain=v1", "--ignored", "--untracked-files=all"),
        "refs": git(path, "for-each-ref", "--format=%(refname) %(objectname)"),
        "config": digest((Path(path) / ".git/config").read_bytes()),
    }


def clone(path, url, commit):
    path.mkdir(parents=True, exist_ok=False)
    git(path, "init", "--quiet", *(["--object-format=sha256"] if len(commit) == 64 else []))
    git(path, "config", "core.autocrlf", "false")
    git(path, "config", "gc.auto", "0")
    git(path, "remote", "add", "origin", url)
    git(path, "fetch", "--depth=1", "--no-tags", "origin", commit)
    git(path, "checkout", "--detach", commit)
    if any(x.startswith("160000 ") for x in git(path, "ls-files", "--stage").splitlines()):
        raise ContractError("Submodules are unsupported")


def quarantine(item):
    item.update(state="QUARANTINED", detected_at=now())
    leases = item.get("leases")
    affected = set(item.get("affected_users", []))
    if leases is None:
        affected.update(item["users"])  # Legacy records lack interval evidence.
    else:
        last_clean = item.get("last_clean_sequence", 0)
        affected.update(
            lease["run_dir"]
            for lease in leases
            if lease.get("ended_sequence") is None or lease["ended_sequence"] > last_clean
        )
    item["affected_users"] = sorted(affected)


class RepoPool:
    def __init__(self, root=None):
        self.home = home(root)
        self.root = self.home / "repos"
        self.control = self.root / ".control"
        self.control.mkdir(parents=True, exist_ok=True)
        marker = self.root / ".managed.json"
        if not marker.exists():
            atomic(marker, {"schema_version": 1, "home": str(self.home)})

    def record_run(self, run_id, run_dir, batch_id="standalone"):
        from mrac_contracts.execution import identifier

        identifier(run_id)
        identifier(batch_id)
        path = checked(self.root, self.root / "writable" / batch_id / run_id)
        record = self.control / "runs" / f"{run_id}.json"
        value = {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "path": str(path),
            "batch_id": batch_id,
            "released": False,
        }
        if record.exists():
            old = read_json(record)
            if any(old[k] != value[k] for k in ("run_id", "run_dir", "path", "batch_id")):
                raise ContractError("Run workspace identity changed")
        else:
            atomic(record, value)
        return path

    def writable_path(self, run_id):
        item = read_json(self.control / "runs" / f"{run_id}.json")
        if item["released"] or item.get("deleted"):
            raise ContractError("Workspace recovery has been released")
        return checked(self.root, Path(item["path"]))

    def supervision(self, run_id, receipt):
        with file_lock(self.control / "metadata" / f"{run_id}.lock"):
            record = self.control / "runs" / f"{run_id}.json"
            item = read_json(record)
            item["supervision"] = receipt
            item["recovery_reserved"] = False
            atomic(record, item)

    def reserve(self, run_id, reserved=True):
        with file_lock(self.control / "metadata" / f"{run_id}.lock", blocking=False):
            record = self.control / "runs" / f"{run_id}.json"
            item = read_json(record)
            if reserved and (item["released"] or item.get("deleted")):
                raise ContractError("Workspace recovery was released")
            item["recovery_reserved"] = reserved
            atomic(record, item)

    def stopped(self, run_dir):
        record = self.control / "runs" / f"{Path(run_dir).name}.json"
        if not record.exists():
            return True  # Unsupervised library callers remain protected by their repo OS lock.
        item = read_json(record).get("supervision")
        if not item or item.get("tree_stopped"):
            return True
        if os.name != "nt":
            return False
        from .windows import job_alive, process_identity

        try:
            if process_identity(item["supervisor"]["pid"]) == item["supervisor"]:
                return False
            return not job_alive(item["job"])
        except OSError:
            return False

    @contextmanager
    def readonly(self, url, commit, run_dir):
        key = digest({"url": url, "commit": commit, "checkout": "autocrlf-false-v1"})
        record = self.control / "shared" / f"{key}.json"
        lock = self.control / "locks" / f"{key}.lock"
        with file_lock(self.control / "prepare" / f"{key}.lock"):
            previous = read_json(record) if record.exists() else None
            if (
                not previous
                or previous.get("deleted")
                or previous["state"] in {"PREPARING", "PREPARE_FAILED"}
            ):
                with file_lock(lock):
                    path = self.root / "shared" / digest(url)[:16] / commit / key[:16] / new_id("g")
                    checked(self.root, path)
                    if previous and previous["state"] in {"PREPARING", "PREPARE_FAILED"}:
                        atomic(
                            self.control / "retired" / f"{key}-{Path(previous['path']).name}.json",
                            previous,
                        )
                    prepared = {
                        "key": key,
                        "path": str(path),
                        "state": "PREPARING",
                        "users": [str(run_dir)],
                        "created_at": now(),
                        "sequence": 0,
                        "last_clean_sequence": 0,
                        "leases": [],
                    }
                    atomic(record, prepared)
                    try:
                        clone(path, url, commit)
                        prepared.update(state="READY", baseline=fingerprint(path))
                    except (OSError, ContractError, subprocess.SubprocessError) as exc:
                        prepared.update(state="PREPARE_FAILED", error=str(exc))
                        atomic(record, prepared)
                        raise
                    atomic(record, prepared)
        with file_lock(lock, shared=True):
            lease_id = new_id("lease")
            with file_lock(self.control / "metadata" / f"{key}.lock"):
                item = read_json(record)
                if item["state"] != "READY" or item.get("deleted"):
                    raise ContractError("Shared repository is quarantined or deleted")
                path = checked(self.root, Path(item["path"]))
                if fingerprint(path) != item["baseline"]:
                    quarantine(item)
                    atomic(record, item)
                    raise ContractError("Shared repository integrity changed")
                if str(run_dir) not in item["users"]:
                    item["users"].append(str(run_dir))
                item["sequence"] = item.get("sequence", 0) + 1
                item["last_clean_sequence"] = item["sequence"]
                item.setdefault("leases", []).append(
                    {
                        "id": lease_id,
                        "run_dir": str(run_dir),
                        "started_at": now(),
                        "started_sequence": item["sequence"],
                    }
                )
                atomic(record, item)
            try:
                yield path, item["baseline"]["files"]
            finally:
                with file_lock(self.control / "metadata" / f"{key}.lock"):
                    current = read_json(record)
                    try:
                        changed = fingerprint(path) != item["baseline"]
                    except (OSError, ContractError):
                        changed = True
                    current["sequence"] = current.get("sequence", 0) + 1
                    if changed:
                        quarantine(current)
                    elif current["state"] == "READY":
                        current["last_clean_sequence"] = current["sequence"]
                    lease = next(row for row in current["leases"] if row["id"] == lease_id)
                    lease.update(ended_at=now(), ended_sequence=current["sequence"])
                    atomic(record, current)

    def invalid_runs(self):
        result = set()
        for path in [
            *(self.control / "shared").glob("*.json"),
            *(self.control / "quarantine").glob("*.json"),
        ]:
            item = read_json(path)
            if item["state"] == "QUARANTINED":
                result.update(item.get("affected_users", item["users"]))
        return result

    def rebuild(self, key):
        from mrac_contracts.execution import identifier

        identifier(key)
        record = self.control / "shared" / f"{key}.json"
        with (
            file_lock(self.control / "locks" / f"{key}.lock", blocking=False),
            file_lock(self.control / "metadata" / f"{key}.lock"),
        ):
            item = read_json(record)
            if any(not self.stopped(user) for user in item["users"]):
                raise ContractError("Repository still has active or uncertain users")
            if item["state"] != "QUARANTINED":
                raise ContractError("Only quarantined generations require rebuild")
            path = checked(self.root, Path(item["path"]))
            item["observed"] = fingerprint(path)
            item["diff"] = git(path, "diff", "--binary", "HEAD")
            atomic(self.control / "quarantine" / f"{key}-{path.name}.json", item)
            item["deleted"] = True  # Retire reference; preserve polluted files for investigation.
            item["retired"] = True
            atomic(record, item)
            return {"retired": str(path), "next_acquisition": "new_generation"}

    def list(self):
        return [
            read_json(p)
            for folder in ("shared", "runs", "retired", "quarantine")
            for p in (self.control / folder).glob("*.json")
        ]

    def resource_events(self):
        events = []
        for path in (self.control / "events").glob("*.json"):
            item = read_json(path)
            if item["status"] == "STARTED":
                resource_id = item["resource"].get("key", item["resource"].get("run_id"))
                with file_lock(self.control / "metadata" / f"{resource_id}.lock"):
                    item = read_json(path)
                    target = checked(self.root, Path(item["resource"]["path"]))
                    if item["status"] == "STARTED" and not target.exists():
                        record = checked(self.control, Path(item["record"]))
                        current = read_json(record)
                        if current["path"] == str(target):
                            current.update(deleted=True, deleted_at=now())
                            atomic(record, current)
                        item.update(status="DONE", completed_at=now())
                        atomic(path, item)
            if item["status"] in {"DONE", "FAILED"}:
                events.append(item)
        return events

    def release(self, run_dir, operation_id):
        run_dir = Path(run_dir).resolve()
        with file_lock(run_dir / ".execution.lock", blocking=False):
            if not self.stopped(run_dir):
                raise ContractError("Process tree has not been confirmed stopped")
            status = read_json(run_dir / "lifecycle.json")
            if status["lifecycle"] not in {"COMPLETED", "FAILED", "CANCELLED"}:
                raise ContractError("Only stopped terminal workspaces may be released")
            record = self.control / "runs" / f"{status['run_id']}.json"
            with file_lock(self.control / "metadata" / f"{run_dir.name}.lock"):
                item = read_json(record)
                if item.get("recovery_reserved"):
                    raise ContractError("Workspace has a pending recovery operation")
                item.update(released=True, release_operation=operation_id)
                atomic(record, item)

    def clean(self, *, apply=False, batch_id=None):
        results = []
        for folder in ("shared", "runs", "retired"):
            for record in (self.control / folder).glob("*.json"):
                item = read_json(record)
                if item.get("deleted") or (batch_id and item.get("batch_id") != batch_id):
                    continue
                path = checked(self.root, Path(item["path"]))
                if not path.exists():
                    continue
                lock = (
                    self.control / "locks" / f"{item['key']}.lock"
                    if folder != "runs"
                    else Path(item["run_dir"]) / ".execution.lock"
                )
                try:
                    resource_id = item.get("key", item.get("run_id"))
                    with (
                        file_lock(lock, blocking=False),
                        file_lock(self.control / "metadata" / f"{resource_id}.lock"),
                    ):
                        item = read_json(record)
                        if item.get("deleted"):
                            continue
                        if folder != "runs":
                            if item["state"] not in {"READY", "PREPARE_FAILED", "PREPARING"}:
                                continue  # Quarantine is retained for explicit investigation.
                            if any(not self.stopped(user) for user in item["users"]):
                                continue
                        else:
                            if item.get("recovery_reserved"):
                                continue
                            if not self.stopped(item["run_dir"]):
                                continue
                            lifecycle = Path(item["run_dir"]) / "lifecycle.json"
                            if not lifecycle.exists():
                                continue
                            status = read_json(lifecycle)
                            if not item["released"] and status["lifecycle"] != "COMPLETED":
                                continue
                            seal_file = Path(item["run_dir"]) / "seal.json"
                            if not seal_file.exists():
                                continue
                            files = read_json(seal_file)["files"]
                            if any(
                                not (Path(item["run_dir"]) / name).is_file()
                                or digest((Path(item["run_dir"]) / name).read_bytes()) != expected
                                for name, expected in files.items()
                            ):
                                raise ContractError(
                                    "Run evidence changed; workspace cannot be cleaned"
                                )
                        # Validate every descendant before a recursive deletion, including junctions.
                        for child in path.rglob("*"):
                            checked(self.root, child)
                        results.append(
                            {
                                "path": str(path),
                                "deleted": apply,
                                "resource_id": item.get("key", item.get("run_id")),
                                "generation": path.name,
                                "state": item.get("state"),
                                "record_sha256": digest(item),
                            }
                        )
                        if apply:
                            operation = {
                                "id": new_id("cleanup"),
                                "kind": "repo_cleanup",
                                "status": "STARTED",
                                "record": str(record),
                                "resource": item,
                                "at": now(),
                            }
                            event_path = self.control / "events" / (operation["id"] + ".json")
                            atomic(event_path, operation)

                            def writable_retry(function, filename, exception):
                                checked(self.root, Path(filename))
                                if not isinstance(exception[1], PermissionError):
                                    raise exception[1]
                                os.chmod(filename, stat.S_IWRITE | stat.S_IREAD)
                                function(filename)

                            try:
                                shutil.rmtree(path, onerror=writable_retry)
                            except OSError as exc:
                                operation.update(status="FAILED", error=str(exc))
                                atomic(event_path, operation)
                                raise
                            item.update(deleted=True, deleted_at=now())
                            atomic(record, item)
                            operation.update(status="DONE", completed_at=now())
                            atomic(event_path, operation)
                except BusyError:
                    continue
        if apply:
            atomic(
                self.control / "cleanups" / f"{new_id('clean')}.json",
                {"at": now(), "items": results},
            )
        return results
