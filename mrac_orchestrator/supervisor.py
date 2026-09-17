import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from mrac_contracts.execution import atomic, now, read_json
from mrac_resources.locks import file_lock


def identity(pid):
    if os.name == "nt":
        from mrac_resources.windows import process_identity

        return process_identity(pid)
    stat = Path(f"/proc/{pid}/stat")
    if not stat.exists():
        return None
    return {"pid": pid, "created": stat.read_text().rsplit(")", 1)[1].split()[19]}


def alive(value):
    try:
        return identity(value["pid"]) == value
    except OSError:
        return None


def tree_alive(name):
    if os.name == "nt":
        from mrac_resources.windows import job_alive

        return job_alive(name)
    raise OSError("Recovery supervision is certified only on Windows")


def run(request_file, command=None):
    request_file = Path(request_file).resolve()
    directory = request_file.parent
    request = read_json(request_file)
    with file_lock(directory / "worker.lock", blocking=False):
        if (directory / "finished.json").exists():
            return
        receipt = {
            "schema_version": 1,
            "started": False,
            "attempt_id": request["attempt_id"],
            "run_id": request["run_id"],
            "request_sha256": request["request_sha256"],
            "supervisor": identity(os.getpid()),
            "job": "Local\\MRAC-" + request["attempt_id"],
            "started_at": now(),
        }
        atomic(directory / "started.json", receipt)
        from mrac_resources.repositories import RepoPool

        pool = RepoPool(request["bench_home"])
        pool.record_run(
            request["run_id"], request["run_dir"], request.get("batch_id") or "standalone"
        )
        pool.supervision(request["run_id"], receipt)
        if (directory / "cancel.json").exists():
            receipt.update(cancelled=True, tree_stopped=True, finished_at=now())
            atomic(directory / "finished.json", receipt)
            pool.supervision(request["run_id"], receipt)
            return
        process = None
        try:
            if os.name != "nt":
                raise OSError("Managed process supervision currently requires Windows 10/11")
            from mrac_resources.windows import JobProcess

            with (
                (directory / "stdout.txt").open("ab", buffering=0) as stdout,
                (directory / "stderr.txt").open("ab", buffering=0) as stderr,
            ):
                process = JobProcess(
                    command
                    or [
                        sys.executable,
                        "-m",
                        "mracbench.machine",
                        "start",
                        "--request",
                        str(request_file),
                    ],
                    stdout,
                    stderr,
                    receipt["job"],
                )
                receipt["runner"] = process.identity
                receipt["started"] = True
                atomic(directory / "started.json", receipt)
                cancellation = None
                while process.poll() is None:
                    if (directory / "cancel.json").exists():
                        cancellation = cancellation or time.monotonic()
                        if time.monotonic() - cancellation >= 10:
                            process.kill()
                    atomic(
                        directory / "heartbeat.json",
                        {"at": now(), "supervisor": receipt["supervisor"]},
                    )
                    time.sleep(0.2)
                exit_code = process.poll()
                if not process.empty():
                    process.kill()
                deadline = time.monotonic() + 15
                while not process.empty():
                    if time.monotonic() > deadline:
                        raise OSError("Cannot prove process tree stopped")
                    time.sleep(0.05)
                receipt.update(
                    exit_code=exit_code, tree_stopped=True, cancelled=cancellation is not None
                )
        except Exception as exc:  # noqa: BLE001 -- persist process supervision failures
            receipt["error"] = str(exc)
            if process is None:
                receipt["tree_stopped"] = True
        finally:
            if process:
                process.close()
        receipt["finished_at"] = now()
        atomic(directory / "finished.json", receipt)
        pool.supervision(request["run_id"], receipt)


def spawn(request_file):
    flags = (
        subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    )
    return subprocess.Popen(
        [sys.executable, "-m", "mrac_orchestrator.supervisor", str(request_file)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        start_new_session=os.name != "nt",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    run(parser.parse_args().request)


if __name__ == "__main__":
    main()
