"""Execution capabilities selected by the saved protocol revision, never by current YAML."""

from dataclasses import dataclass
from functools import cache
from typing import Literal


@dataclass(frozen=True)
class RevisionPolicy:
    executable: bool = True
    repair_mode: Literal["markdown", "json", "candidate-mcp", "checkout"] = "markdown"
    allow_fence: bool = False
    allow_trailing_fence: bool = False
    bind_invocation: bool = False
    preflight_event_only: bool = False
    preflight_allow_query: bool = False
    compact_checkout: bool = False
    literal_source: bool = False
    review_max_reason_chars: int | None = 500
    repository_audit_mcp: bool = False
    historical_repository_review: bool = False
    managed_continue: bool = False
    finish_last_audit_repair: bool = True

    @property
    def file_repair(self) -> bool:
        return self.repair_mode in {"candidate-mcp", "checkout"}

    @property
    def writable_checkout(self) -> bool:
        return self.repair_mode == "checkout"


@cache
def revision_policy(workflow: str, version: int) -> RevisionPolicy:
    if workflow == "spec-init-freeze":
        mode = (
            "checkout"
            if version >= 10
            else "candidate-mcp"
            if version >= 6
            else "json"
            if version >= 5
            else "markdown"
        )
        return RevisionPolicy(
            executable=version >= 4,
            repair_mode=mode,
            allow_fence=version >= 7,
            allow_trailing_fence=version >= 8,
            bind_invocation=version >= 12,
            preflight_event_only=version >= 7,
            preflight_allow_query=version >= 9,
            compact_checkout=version >= 12,
            literal_source=version >= 14,
            review_max_reason_chars=None if version >= 15 else 500,
            # Preserve the existing public machine contract for historical runs.
            managed_continue=version in {4, 5, 6, 7, 8, 9, 10, 11},
        )
    if workflow == "repository-spec-freeze":
        return RevisionPolicy(
            executable=version in {2, 3, 4, 5},
            repair_mode="checkout" if version >= 3 else "json",
            repository_audit_mcp=version >= 4,
            historical_repository_review=version == 1,
            finish_last_audit_repair=version < 5,
        )
    if workflow == "exec-mrac":
        return RevisionPolicy(repair_mode="checkout")
    return RevisionPolicy()
