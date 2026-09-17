import json
import subprocess
import sys
from pathlib import Path

from mrac_contracts.execution import ContractError, atomic, digest, envelope, read_json
from mrac_resources.home import copy_package, inventory

from ..supervisor import alive, spawn, tree_alive


def machine(command, *args):
    process = subprocess.run(
        [sys.executable, "-m", "mracbench.machine", command, *map(str, args)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    try:
        result = json.loads(process.stdout)
    except ValueError as exc:
        raise ContractError(f"Invalid runner response: {process.stderr}") from exc
    if process.returncode or "error" in result and command != "inspect":
        raise ContractError(result.get("error", process.stderr))
    return result


class MRACBackend:
    def __init__(self, home):
        self.home = Path(home)

    def freeze(
        self, case, protocol_id, project, directory, settings, *, spec_file=None, codex="codex"
    ):
        directory = Path(directory)
        bundle = directory / "bundle"
        copy_package(case["path"], bundle / "cases" / case["source_id"])
        copy_package(Path(project) / "protocols" / protocol_id, bundle / "protocols" / protocol_id)
        if spec_file:
            atomic(bundle / "execution-spec.md", Path(spec_file).read_bytes(), raw=True)
        query = directory / "validate.json"
        settings = {**settings, "case_id": case["source_id"], "protocol_id": protocol_id}
        atomic(query, {"bundle": str(bundle), "settings": settings})
        effective = machine("validate", "--request", query)
        capabilities = machine("capabilities", "--codex-executable", codex)
        if not capabilities["agent_version"]:
            raise ContractError("Cannot pin Codex executable version")
        manifest = inventory(bundle)
        value = {
            "bundle": str(bundle),
            "bundle_manifest": manifest,
            "settings": effective,
            "code_identity": capabilities["code_identity"],
            "agent_version": capabilities["agent_version"],
            "codex_executable": codex,
            "bench_home": str(self.home),
            "case": {
                k: case[k] for k in ("number", "case_key", "name", "version", "source_id", "sha256")
            },
        }
        value["run_spec_sha256"] = digest(value)
        return value

    def request(self, task, attempt):
        data = {
            **task["execution"],
            "run_id": task["run_id"],
            "run_dir": task["run_dir"],
            "batch_id": task.get("batch_id"),
            "attempt_id": attempt["id"],
            "operation_id": task["operation_id"],
            "operation": task.get("operation", "start"),
        }
        if data["operation"] != "start":
            public = self.inspect(task["run_dir"])
            data["expected_revision"] = public["revision"]
        if task.get("input_file"):
            data.update(input_file=task["input_file"], input_sha256=task["input_sha256"])
        return envelope(data)

    def launch(self, path):
        return spawn(path)

    def inspect(self, run_dir):
        return machine("inspect", "--run-dir", run_dir)

    def cancelled(self, task):
        from mrac_resources.repositories import RepoPool

        if (Path(task["run_dir"]) / "lifecycle.json").exists():
            public = self.inspect(task["run_dir"])
            machine(
                "abort",
                "--run-dir",
                task["run_dir"],
                "--operation-id",
                task.get("cancel_operation_id", "cancel-" + task["run_id"]),
                "--expected-revision",
                public["revision"],
            )
            RepoPool(self.home).reserve(task["run_id"], False)

    def reserve(self, task):
        from mrac_resources.repositories import RepoPool

        RepoPool(self.home).reserve(task["run_id"])

    def observe(self, task, attempt):
        directory = Path(attempt["directory"])
        finish = directory / "finished.json"
        if not finish.exists():
            started = directory / "started.json"
            if not started.exists():
                return {"state": "UNKNOWN"}
            receipt = read_json(started)
            living = alive(receipt["supervisor"])
            if living is True:
                return {"state": "RUNNING"}
            if living is None:
                return {"state": "UNKNOWN"}
            try:
                if tree_alive(receipt["job"]):
                    return {"state": "UNKNOWN"}
            except OSError:
                return {"state": "UNKNOWN"}
            return {"state": "LOST", "error": "Supervisor stopped unexpectedly"}
        receipt = read_json(finish)
        if receipt.get("schema_version") != 1:
            raise ContractError("Unsupported completion receipt schema")
        request = read_json(directory / "request.json")
        if (
            receipt["attempt_id"] != attempt["id"]
            or receipt["request_sha256"] != request["request_sha256"]
        ):
            raise ContractError("Mismatched completion receipt")
        if not receipt.get("tree_stopped"):
            try:
                if alive(receipt["supervisor"]) is False and not tree_alive(receipt["job"]):
                    return {
                        "state": "LOST",
                        "error": receipt.get("error", "Supervisor cleanup interrupted"),
                    }
            except OSError:
                pass
            return {"state": "UNKNOWN"}
        if receipt.get("cancelled"):
            return {"state": "CANCELLED"}
        public_path = Path(task["run_dir"]) / "lifecycle.json"
        if not public_path.exists():
            return {"state": "LOST", "error": receipt.get("error", "No runner receipt")}
        result = self.inspect(task["run_dir"])
        if result["attempt_id"] != attempt["id"] or result["run_id"] != task["run_id"]:
            return {"state": "LOST", "error": "Runner did not start this attempt"}
        if result["lifecycle"] == "RUNNING":
            return {"state": "LOST", "error": "Runner interrupted", "public": result}
        if not result.get("checkpoint_valid"):
            return {
                "state": "FAILED",
                "error": result.get("error", "Invalid checkpoint"),
                "public": result,
            }
        for artifact in result.get("artifacts", []):
            if (
                digest((Path(task["run_dir"]) / artifact["path"]).read_bytes())
                != artifact["sha256"]
            ):
                return {"state": "FAILED", "error": "Artifact digest changed"}
        return {"state": result["lifecycle"], "public": result}
