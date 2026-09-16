import json
import os
import shutil
import signal
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

from .models import AgentRequest, AgentResult
from .runs import utc_now, write_json


def resolve_command(executable: str) -> list[str]:
    found = shutil.which(executable)
    if found is None:
        raise FileNotFoundError(f"Codex executable not found: {executable}")
    path = Path(found)
    if os.name == "nt" and path.suffix.lower() in (".cmd", ".bat", ".ps1"):
        # npm's Windows shim is a shell program. Invoke its JS entry point directly
        # with an argv list so prompts/paths never undergo shell interpolation.
        entry = path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        node = shutil.which("node")
        if entry.is_file() and node:
            return [node, str(entry)]
        raise OSError("Use a native codex.exe or the standard npm Codex installation")
    return [str(path)]


def kill_tree(process: subprocess.Popen):
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=15)


class CodexExecAdapter:
    agent_type = "codex_exec"

    def __init__(self, executable: str = "codex", command: Sequence[str] | None = None):
        self.executable = executable
        self._command = list(command) if command else None

    def command(self) -> list[str]:
        return self._command or resolve_command(self.executable)

    def version(self) -> str | None:
        try:
            result = subprocess.run(
                [*self.command(), "--version"],
                capture_output=True,
                timeout=10,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    def run(self, request: AgentRequest) -> AgentResult:
        start = time.monotonic()
        raw = request.raw_dir
        raw.mkdir(parents=True, exist_ok=True)
        final_path = raw / "final.txt"
        result = AgentResult()
        invocation = {
            "started_at": utc_now(),
            "ended_at": None,
            "started": False,
            "cwd": str(request.workspace),
            "model": request.model,
            "timeout_seconds": request.timeout_seconds,
        }
        # Logs exist even if resolution or process creation fails.
        (raw / "stdout.txt").touch()
        (raw / "stderr.txt").touch()
        (raw / "request.txt").write_text(request.prompt, encoding="utf-8")
        process = None
        try:
            if not request.readonly:
                raise OSError("Spec-MRAC requires read-only execution")
            command = [
                *self.command(),
                "exec",
                "--ignore-user-config",
                "--ignore-rules",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--json",
                "--color",
                "never",
                "-c",
                'approval_policy="never"',
                "-c",
                'web_search="disabled"',
                "-c",
                "project_doc_max_bytes=0",
                "-c",
                "features.multi_agent=false",
                "--cd",
                str(request.workspace),
                "--output-last-message",
                str(final_path),
            ]
            if request.model:
                command += ["--model", request.model]
            command.append("-")
            invocation["command"] = command
            write_json(raw / "invocation.json", invocation)
            with (
                (raw / "stdout.txt").open("wb") as stdout,
                (raw / "stderr.txt").open("wb") as stderr,
            ):
                process = subprocess.Popen(
                    command,
                    cwd=request.workspace,
                    stdin=subprocess.PIPE,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=os.name != "nt",
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                )
                result.started = True
                invocation.update(started=True, pid=process.pid)
                write_json(raw / "invocation.json", invocation)
                try:
                    process.communicate(
                        request.prompt.encode("utf-8"), timeout=request.timeout_seconds
                    )
                except subprocess.TimeoutExpired:
                    result.error_type = "TIMEOUT"
                    result.error_message = f"Agent exceeded {request.timeout_seconds} seconds"
                    kill_tree(process)
                except KeyboardInterrupt:
                    result.error_type = "INTERNAL_ERROR"
                    result.error_message = "Run interrupted by user"
                    kill_tree(process)
                result.exit_code = process.returncode
            if result.exit_code != 0 and result.error_type is None:
                result.error_type = "AGENT_ERROR"
                result.error_message = (
                    f"Codex exited with code {result.exit_code}; see raw stderr/stdout"
                )
            if final_path.exists():
                result.final_text = final_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
            if process is not None and process.poll() is None:
                kill_tree(process)
            result.error_type = "AGENT_ERROR"
            result.error_message = str(exc)
        finally:
            result.duration_seconds = time.monotonic() - start
            result.stdout = (raw / "stdout.txt").read_text(encoding="utf-8", errors="replace")
            result.stderr = (raw / "stderr.txt").read_text(encoding="utf-8", errors="replace")
            invocation.update(
                ended_at=utc_now(),
                exit_code=result.exit_code,
                duration_seconds=result.duration_seconds,
                error_type=result.error_type,
                error_message=result.error_message,
            )
            result.metadata = invocation
            # Usage is optional executor metadata, never an MRAC decision input.
            for line in result.stdout.splitlines():
                try:
                    event = json.loads(line)
                    if isinstance(event, dict) and event.get("type") == "turn.completed":
                        result.usage = event.get("usage")
                except ValueError:
                    pass
            write_json(raw / "invocation.json", invocation)
        return result
