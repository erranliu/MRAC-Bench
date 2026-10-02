import json
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
        if self.provider is None:
            return []
        provider = self.provider
        values = {"model_provider": provider["id"]}
        for key in ("name", "base_url", "wire_api", "env_key"):
            if provider.get(key) is not None:
                values[f"model_providers.{provider['id']}.{key}"] = provider[key]
        values[f"model_providers.{provider['id']}.requires_openai_auth"] = False
        if "model_catalog" in provider:
            path = raw / "model-catalog.json"
            write_json(path, provider["model_catalog"])
            values["model_catalog_json"] = str(path.resolve())
        return [
            part
            for key, value in values.items()
            for part in ("-c", f"{key}={json.dumps(value, ensure_ascii=False)}")
        ]

    @staticmethod
    def mcp_arguments(servers):
        values = {}
        for name, config in (servers or {}).items():
            if not name.replace("_", "").isalnum() or not isinstance(config, dict):
                raise ValueError("Invalid MCP server configuration")
            if not isinstance(config.get("command"), str) or not config["command"]:
                raise ValueError(f"MCP server {name} requires an executable command")
            if not isinstance(config.get("args", []), list) or any(
                not isinstance(value, str) for value in config.get("args", [])
            ):
                raise ValueError(f"MCP server {name} args must be strings")
            for key, value in config.items():
                if key not in {
                    "command",
                    "args",
                    "enabled",
                    "startup_timeout_sec",
                    "tool_timeout_sec",
                }:
                    raise ValueError(f"Unsupported MCP server option: {key}")
                values[f"mcp_servers.{name}.{key}"] = value
        return [
            part
            for key, value in values.items()
            for part in ("-c", f"{key}={json.dumps(value, ensure_ascii=False)}")
        ]

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
            command = [
                *self.command(),
                "exec",
                "--ignore-user-config",
                "--ignore-rules",
                "--ephemeral",
                "--sandbox",
                "read-only" if request.readonly else "workspace-write",
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
                "-c",
                "features.apps=false",
                "--cd",
                str(request.workspace),
                "--output-last-message",
                str(output_path),
            ]
            if os.name == "nt":
                # --ignore-user-config also drops the native Windows sandbox
                # implementation. Select it explicitly so workspace-write can
                # execute file tools inside the isolated repair checkout.
                command += ["-c", f'windows.sandbox="{self.windows_sandbox}"']
                invocation["windows_sandbox"] = self.windows_sandbox
            if request.output_schema is not None:
                schema_path = raw / "output-schema.json"
                write_json(schema_path, request.output_schema)
                command += ["--output-schema", str(schema_path)]
            command += self.provider_arguments(raw)
            if uses_openrouter(self.provider):
                if not secret:
                    raise ProviderError("OpenRouter accounting requires an env_key credential")
                collector = OpenRouterAccounting(
                    self.provider, secret, raw, request.timeout_seconds
                )
                endpoint = collector.__enter__()
                accounting = collector
                command += [
                    "-c",
                    f"model_providers.{self.provider['id']}.base_url={json.dumps(endpoint)}",
                ]
            command += self.mcp_arguments(request.mcp_servers)
            if request.model:
                command += ["--model", request.model]
            if request.reasoning_effort:
                command += ["-c", f'model_reasoning_effort="{request.reasoning_effort}"']
            if request.skip_git_repo_check:
                command.append("--skip-git-repo-check")
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
            if (
                result.error_type is None
                and "blocked by policy" in (result.stdout + "\n" + result.stderr).casefold()
            ):
                result.error_type = "EXECUTION_POLICY_ERROR"
                result.error_message = (
                    "Codex could not execute a requested repository command because the "
                    "execution policy rejected it; see raw stderr/stdout"
                )
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
                    if (
                        isinstance(event, dict)
                        and event.get("type") == "thread.started"
                        and isinstance(event.get("thread_id"), str)
                    ):
                        invocation["thread_id"] = event["thread_id"]
                    item = event.get("item") if isinstance(event, dict) else None
                    if (
                        isinstance(item, dict)
                        and event.get("type") == "item.completed"
                        and item.get("type") == "mcp_tool_call"
                        and item.get("status") == "failed"
                        and result.error_type in {None, "AGENT_ERROR"}
                        and "timed out awaiting tools/call"
                        in str((item.get("error") or {}).get("message", ""))
                    ):
                        result.error_type = "EXECUTION_ENVIRONMENT_ERROR"
                        result.error_message = f"MCP tool timed out: {item.get('server')}/{item.get('tool')}; see raw stdout"
                    if (
                        isinstance(item, dict)
                        and event.get("type") == "item.completed"
                        and item.get("type") in {"command_execution", "shell"}
                    ):
                        invocation.setdefault("command_executions", []).append(
                            {
                                "command": str(item.get("command", ""))[:4096],
                                "exit_code": item.get("exit_code"),
                            }
                        )
                        if (
                            result.error_type in {None, "AGENT_ERROR"}
                            and item.get("status") == "failed"
                            and "Failed to create unified exec process: sandbox provisioning failed"
                            in str(item.get("aggregated_output", ""))
                        ):
                            result.error_type = "EXECUTION_ENVIRONMENT_ERROR"
                            result.error_message = (
                                "Codex could not provision the sandbox for a repository command; "
                                "see raw stdout and the Windows sandbox setup logs"
                            )
                except ValueError:
                    pass
            invocation.update(error_type=result.error_type, error_message=result.error_message)
            write_json(raw / "invocation.json", invocation)
        return result
