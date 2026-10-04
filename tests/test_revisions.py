from dataclasses import FrozenInstanceError, replace

import pytest

from mracbench.protocol import load_protocol, protocol_from_snapshots
from mracbench.revisions import revision_policy


@pytest.mark.parametrize(
    "version,mode,fence,trailing,bind,query,compact,literal,reason_limit",
    [
        (3, "markdown", False, False, False, False, False, False, 500),
        (4, "markdown", False, False, False, False, False, False, 500),
        (5, "json", False, False, False, False, False, False, 500),
        (6, "candidate-mcp", False, False, False, False, False, False, 500),
        (7, "candidate-mcp", True, False, False, False, False, False, 500),
        (8, "candidate-mcp", True, True, False, False, False, False, 500),
        (9, "candidate-mcp", True, True, False, True, False, False, 500),
        (10, "checkout", True, True, False, True, False, False, 500),
        (11, "checkout", True, True, False, True, False, False, 500),
        (12, "checkout", True, True, True, True, True, False, 500),
        (13, "checkout", True, True, True, True, True, False, 500),
        (14, "checkout", True, True, True, True, True, True, 500),
        (15, "checkout", True, True, True, True, True, True, None),
    ],
)
def test_simple_revision_contracts(
    version,
    mode,
    fence,
    trailing,
    bind,
    query,
    compact,
    literal,
    reason_limit,
):
    policy = revision_policy("spec-init-freeze", version)
    assert policy.repair_mode == mode
    assert policy.allow_fence == fence
    assert policy.allow_trailing_fence == trailing
    assert policy.bind_invocation == bind
    assert policy.preflight_allow_query == query
    assert policy.compact_checkout == compact
    assert policy.literal_source == literal
    assert policy.review_max_reason_chars == reason_limit
    assert policy.executable == (version >= 4)
    assert policy.file_repair == (mode in {"candidate-mcp", "checkout"})
    assert policy.preflight_event_only == (version >= 7)
    assert policy.managed_continue == (4 <= version <= 11)


@pytest.mark.parametrize(
    "version,executable,checkout,mcp",
    [
        (1, False, False, False),
        (2, True, False, False),
        (3, True, True, False),
        (4, True, True, True),
        (5, False, True, True),
    ],
)
def test_repository_revisions(version, executable, checkout, mcp):
    policy = revision_policy("repository-spec-freeze", version)
    assert policy.executable == executable
    assert policy.writable_checkout == checkout
    assert policy.repository_audit_mcp == mcp
    assert policy.historical_repository_review == (version == 1)


def test_policy_follows_saved_snapshot_even_after_current_protocol_changes(project):
    historical = load_protocol(project, "spec-flow-simple-v1")
    assert historical.version == 9
    changed = replace(historical, version=15)
    assert changed.policy.repair_mode == "checkout"
    assert changed.policy.review_max_reason_chars is None
    restored = protocol_from_snapshots(historical.id, historical.snapshots)
    assert restored.policy == historical.policy and restored.version == 9
    assert restored.policy is historical.policy
    with pytest.raises(FrozenInstanceError):
        restored.policy.repair_mode = "checkout"


def test_current_protocols_use_current_capabilities(current_project):
    simple = load_protocol(current_project, "spec-flow-simple-v1")
    assert simple.version == 15 and simple.policy.literal_source
    assert simple.policy.review_max_reason_chars is None
    repository = load_protocol(current_project, "spec-mrac-v2")
    assert repository.version == 4 and repository.policy.repository_audit_mcp
    assert load_protocol(current_project, "exec-mrac-v1").policy.writable_checkout
