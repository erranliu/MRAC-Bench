from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class BenchError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Case:
    id: str
    version: int
    repository_url: str
    commit: str
    task: str
    protocol_id: str
    max_audit_rounds: int | None
    timeout_seconds: int
    snapshots: dict[str, bytes]


@dataclass(frozen=True)
class ProtocolDefinition:
    id: str
    version: int
    max_audit_rounds: int
    prompts: dict[str, str]
    snapshots: dict[str, bytes]


@dataclass(frozen=True)
class RunConfig:
    project_root: Path
    case_id: str
    runs_dir: Path
    workspace_dir: Path
    model: str | None = None
    max_rounds: int | None = None
    timeout_seconds: int | None = None


@dataclass(frozen=True)
class AgentRequest:
    prompt: str
    workspace: Path
    raw_dir: Path
    timeout_seconds: int
    model: str | None = None
    readonly: bool = True


@dataclass
class AgentResult:
    final_text: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_seconds: float = 0.0
    started: bool = False
    error_type: str | None = None
    error_message: str | None = None
    usage: dict | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.started and self.exit_code == 0 and self.error_type is None


class AgentAdapter(Protocol):
    """Minimal execution boundary; no knowledge of cases or MRAC stages."""

    agent_type: str

    def version(self) -> str | None: ...

    def run(self, request: AgentRequest) -> AgentResult: ...
