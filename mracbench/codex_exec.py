import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

from mrac_contracts.providers import (
    ProviderError,
    credential,
    normalize_provider,
    provider_identity,
    validate_selection,
)

from .codex_command import build_command, command_files, mcp_arguments, provider_arguments
from .codex_events import summarize_events
from .models import AgentRequest, AgentResult
from .openrouter_accounting import OpenRouterAccounting, uses_openrouter
from .redaction import RedactedPipe
from .runs import utc_now, write_json

WINDOWS_SANDBOX_MODES = ("elevated", "unelevated")


def saved_windows_sandbox(path):
    from .cases import load_yaml

    metadata = path / "run.yaml"
    if not metadata.exists():
        return "elevated"
    return (
        load_yaml(metadata.read_bytes(), "run.yaml")
        .get("effective_config", {})
        .get("windows_sandbox", "elevated")
    )


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

    def __init__(
        self,
        executable: str = "codex",
        command: Sequence[str] | None = None,
        *,
        provider=None,
        windows_sandbox="elevated",
    ):
        self.executable = executable
        self._command = list(command) if command else None
        self.provider = normalize_provider(provider)
        if windows_sandbox not in WINDOWS_SANDBOX_MODES:
            raise ValueError("windows_sandbox must be elevated or unelevated")
        self.windows_sandbox = windows_sandbox

    def provider_arguments(self, raw):
        if self.provider and "model_catalog" in self.provider:
            write_json(raw / "model-catalog.json", self.provider["model_catalog"])
        return provider_arguments(self.provider, raw)

    mcp_arguments = staticmethod(mcp_arguments)

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
            "reasoning_effort": request.reasoning_effort,
            "timeout_seconds": request.timeout_seconds,
            "readonly": request.readonly,
            "apps_enabled": False,
        }
        # Logs exist even if resolution or process creation fails.
        (raw / "stdout.txt").touch()
        (raw / "stderr.txt").touch()
        (raw / "request.txt").write_text(request.prompt, encoding="utf-8")
        process = None
        final_directory = None
        output_path = final_path
        secret = None
        accounting = None
        try:
            validate_selection(self.provider, request.model, request.reasoning_effort)
            secret = credential(self.provider)
            if secret:
                final_directory = tempfile.TemporaryDirectory(prefix="mrac-provider-final-")
                output_path = Path(final_directory.name) / "final.txt"
            if self.provider:
                invocation["provider"] = provider_identity(self.provider)
            base_command = self.command()
            if os.name == "nt":
                invocation["windows_sandbox"] = self.windows_sandbox
            for path, content in command_files(request, self.provider).items():
                write_json(path, content)
            endpoint = None
            if uses_openrouter(self.provider):
                if not secret:
                    raise ProviderError("OpenRouter accounting requires an env_key credential")
                collector = OpenRouterAccounting(
                    self.provider, secret, raw, request.timeout_seconds
                )
                endpoint = collector.__enter__()
                accounting = collector
            command = build_command(
                base_command,
                request,
                output_path,
                provider=self.provider,
                windows=os.name == "nt",
                windows_sandbox=self.windows_sandbox,
                accounting_endpoint=endpoint,
            )
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
                    stdout=subprocess.PIPE if secret else stdout,
                    stderr=subprocess.PIPE if secret else stderr,
                    start_new_session=os.name != "nt",
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                )
                readers = []
                if secret:
                    readers = [
                        RedactedPipe(process.stdout, stdout, secret),
                        RedactedPipe(process.stderr, stderr, secret),
                    ]
                    # communicate() owns stdin/wait; the readers exclusively own output pipes.
                    process.stdout = process.stderr = None
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
                finally:
                    for reader in readers:
                        reader.finish()
                result.exit_code = process.returncode
            if result.exit_code != 0 and result.error_type is None:
                result.error_type = "AGENT_ERROR"
                result.error_message = (
                    f"Codex exited with code {result.exit_code}; see raw stderr/stdout"
                )
            if output_path.exists():
                result.final_text = output_path.read_text(encoding="utf-8")
                if secret:
                    result.final_text = result.final_text.replace(secret, "[REDACTED]")
                    final_path.write_text(result.final_text, encoding="utf-8")
        except (OSError, UnicodeError, subprocess.SubprocessError, ProviderError) as exc:
            if process is not None and process.poll() is None:
                kill_tree(process)
            result.error_type = (
                "PROVIDER_ERROR" if isinstance(exc, ProviderError) else "AGENT_ERROR"
            )
            result.error_message = str(exc).replace(secret, "[REDACTED]") if secret else str(exc)
        finally:
            if accounting is not None:
                try:
                    accounting.__exit__()
                except OSError:
                    invocation["accounting_issue"] = "provider_usage_persistence_failed"
            if final_directory:
                final_directory.cleanup()
            result.duration_seconds = time.monotonic() - start
            result.stdout = (raw / "stdout.txt").read_text(encoding="utf-8", errors="replace")
            result.stderr = (raw / "stderr.txt").read_text(encoding="utf-8", errors="replace")
            events = summarize_events(
                result.stdout,
                result.stderr,
                error_type=result.error_type,
                error_message=result.error_message,
            )
            result.usage = events.usage
            result.error_type, result.error_message = events.error_type, events.error_message
            if events.thread_id is not None:
                invocation["thread_id"] = events.thread_id
            if events.command_executions:
                invocation["command_executions"] = events.command_executions
            invocation.update(
                ended_at=utc_now(),
                exit_code=result.exit_code,
                duration_seconds=result.duration_seconds,
                error_type=result.error_type,
                error_message=result.error_message,
            )
            result.metadata = invocation
            write_json(raw / "invocation.json", invocation)
        return result
