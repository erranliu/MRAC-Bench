"""Shared effective limits for standalone runs and frozen managed inputs."""

from dataclasses import dataclass
from pathlib import Path

from .cases import positive_int
from .models import BenchError, Case, ProtocolDefinition


@dataclass(frozen=True)
class EffectiveLimits:
    max_audit_rounds: int | None
    timeout_seconds: int
    readonly: bool


def resolve_limits(
    case: Case,
    protocol: ProtocolDefinition,
    *,
    max_rounds: int | None = None,
    timeout_seconds: int | None = None,
    spec_file: Path | None = None,
) -> EffectiveLimits:
    if protocol.workflow == "exec-mrac":
        if spec_file is None:
            raise BenchError("CASE_ERROR", "exec-mrac requires an explicit --spec-file")
        if max_rounds is not None and (type(max_rounds) is not int or max_rounds != 6):
            raise BenchError("CASE_ERROR", "exec-mrac runs in six-audit batches; resume adds six")
        maximum = 6
    elif spec_file is not None:
        raise BenchError("CASE_ERROR", "run --spec-file is supported only by exec-mrac")
    elif protocol.workflow == "repository-spec-freeze":
        maximum = max_rounds if max_rounds is not None else protocol.max_audit_rounds
        if maximum is not None:
            maximum = positive_int(maximum, "max_audit_rounds")
    else:
        maximum = positive_int(
            max_rounds
            if max_rounds is not None
            else (
                case.max_audit_rounds
                if case.max_audit_rounds is not None
                else protocol.max_audit_rounds
            ),
            "max_audit_rounds",
        )
    timeout = positive_int(
        case.timeout_seconds if timeout_seconds is None else timeout_seconds,
        "agent_timeout_seconds",
    )
    return EffectiveLimits(maximum, timeout, readonly=not protocol.policy.writable_checkout)
