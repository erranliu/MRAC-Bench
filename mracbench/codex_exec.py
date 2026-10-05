import os
import shutil
import signal
import subprocess
import tempfile
import threading
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
from .invocation_monitor import InvocationMonitor
from .models import AgentRequest, AgentResult
from .openrouter_accounting import OpenRouterAccounting, uses_openrouter
from .redaction import RedactedPipe
from .runs import utc_now, write_json

WINDOWS_SANDBOX_MODES = ("elevated", "unelevated")
HARNESS_VERSION = 2


def send_prompt(process, prompt):
    try:
        process.stdin.write(prompt.encode("utf-8"))
    except (BrokenPipeError, OSError):
        pass  # Early exits are classified by the process result, not by stdin.
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass


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
        completion_grace_seconds=30,
        tool_timeout_seconds=300,
    ):
        self.executable = executable
        self._command = list(command) if command else None
        self.provider = normalize_provider(provider)
        if windows_sandbox not in WINDOWS_SANDBOX_MODES:
            raise ValueError("windows_sandbox must be elevated or unelevated")
        self.windows_sandbox = windows_sandbox
        if completion_grace_seconds <= 0 or tool_timeout_seconds <= 0:
            raise ValueError("Completion grace and tool timeout must be positive")
        self.completion_grace_seconds = completion_grace_seconds
        self.tool_timeout_seconds = tool_timeout_seconds

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
        final_path.unlink(missing_ok=True)
        result = AgentResult()
        prompt = request.prompt
        if os.name == "nt":
            prompt = (
                "## Runner execution environment\n"
                "The native shell is PowerShell on Windows. Use PowerShell-compatible commands; "
                "Bash heredocs such as python - <<'PY' do not work here. "
                "Read and write text explicitly as UTF-8; use literal file paths. "
                "Use supplied Spec file tools for UTF-8 edits when available.\n\n"
            ) + prompt
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
            "harness_version": HARNESS_VERSION,
            "completion_grace_seconds": self.completion_grace_seconds,
            "tool_timeout_seconds": self.tool_timeout_seconds,
        }
        # Logs exist even if resolution or process creation fails.
        (raw / "stdout.txt").touch()
        (raw / "stderr.txt").touch()
        (raw / "request.txt").write_text(prompt, encoding="utf-8")
        process = None
        final_directory = None
        output_path = final_path
        secret = None
        accounting = None
        monitor = None
        monitor_finished = None
        forced_cleanup = False
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
                    # Readers exclusively own output pipes. The monitor observes redacted files.
                    process.stdout = process.stderr = None
                result.started = True
                invocation.update(started=True, pid=process.pid)
                write_json(raw / "invocation.json", invocation)
                launched = time.monotonic()
                monitor = InvocationMonitor(launched)
                writer = threading.Thread(target=send_prompt, args=(process, prompt), daemon=True)
                writer.start()
                next_progress = launched
                try:
                    while process.poll() is None:
                        current = time.monotonic()
                        monitor.read(raw / "stdout.txt", current)
                        if current >= next_progress:
                            invocation["progress"] = monitor.snapshot(current)
                            write_json(raw / "invocation.json", invocation)
                            next_progress = current + 1
                        completed = monitor.completed_at
                        if completed is not None and (
                            current - completed >= self.completion_grace_seconds
                            or current - launched >= request.timeout_seconds
                        ):
                            forced_cleanup = True
                            invocation["cleanup_reason"] = "process_remained_after_completed_turn"
                            kill_tree(process)
                            break
                        if completed is None:
                            overdue = monitor.overdue_tool(current, self.tool_timeout_seconds)
                            if overdue is not None:
                                result.error_type = "EXECUTION_ENVIRONMENT_ERROR"
                                result.error_message = (
                                    f"Tool {overdue} exceeded {self.tool_timeout_seconds} seconds"
                                )
                                invocation["cleanup_reason"] = "tool_timeout"
                                kill_tree(process)
                                break
                            if current - launched >= request.timeout_seconds:
                                result.error_type = "TIMEOUT"
                                result.error_message = (
                                    f"Agent exceeded {request.timeout_seconds} seconds"
                                )
                                kill_tree(process)
                                break
                        time.sleep(0.05)
                except KeyboardInterrupt:
                    result.error_type = "INTERNAL_ERROR"
                    result.error_message = "Run interrupted by user"
                    kill_tree(process)
                finally:
                    writer.join(timeout=1)
                    for reader in readers:
                        reader.finish()
                result.exit_code = process.returncode
            if result.exit_code != 0 and result.error_type is None and not forced_cleanup:
                result.error_type = "AGENT_ERROR"
                result.error_message = (
                    f"Codex exited with code {result.exit_code}; see raw stderr/stdout"
                )
            if output_path.exists():
                result.final_text = output_path.read_text(encoding="utf-8")
                if secret:
                    result.final_text = result.final_text.replace(secret, "[REDACTED]")
                    final_path.write_text(result.final_text, encoding="utf-8")
            if monitor is not None:
                monitor.read(raw / "stdout.txt", time.monotonic())
                if forced_cleanup:
                    invocation["validated_completed_turn"] = monitor.verifies(result.final_text)
                    if not invocation["validated_completed_turn"]:
                        result.error_type = "EXECUTION_ENVIRONMENT_ERROR"
                        result.error_message = (
                            "Codex did not exit after completion; final reply or tool completion "
                            "could not be verified"
                        )
                elif result.error_type is None and monitor.completed_at is not None:
                    if monitor.pending:
                        result.error_type = "EXECUTION_ENVIRONMENT_ERROR"
                        result.error_message = "Codex exited with unfinished tools after completion"
        except (OSError, UnicodeError, subprocess.SubprocessError, ProviderError) as exc:
            if process is not None and process.poll() is None:
                kill_tree(process)
            result.error_type = (
                "PROVIDER_ERROR" if isinstance(exc, ProviderError) else "AGENT_ERROR"
            )
            result.error_message = str(exc).replace(secret, "[REDACTED]") if secret else str(exc)
        finally:
            if monitor is not None:
                monitor_finished = time.monotonic()
                monitor.read(raw / "stdout.txt", monitor_finished)
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
            if monitor is not None:
                invocation["progress"] = monitor.snapshot(monitor_finished)
                invocation["startup_seconds"] = round(monitor.start - start, 3)
                invocation["teardown_seconds"] = round(time.monotonic() - monitor_finished, 3)
            invocation["diagnostics"] = {
                "patch_verification_errors": result.stderr.count("apply_patch verification failed"),
                "unknown_process_errors": result.stderr.count("Unknown process id"),
                "sandbox_denials": result.stderr.count("recorded sandbox violation"),
            }
            invocation["forced_cleanup"] = forced_cleanup
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
