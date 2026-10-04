import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from mrac_resources.locks import BusyError
from mracbench.evidence import Evidence, digest
from mracbench.models import BenchError
from mracbench.session import (
    CheckpointStore,
    reconcile_invocation,
    run_lock,
    session_lock,
)


def test_evidence_writes_preserve_bytes_and_refuse_replacement_or_escape(tmp_path):
    evidence = Evidence(tmp_path)
    content = b"\xef\xbb\xbf# Spec\r\n"
    assert evidence.write_bytes("artifacts/spec.md", content) == "artifacts/spec.md"
    assert evidence.known["artifacts/spec.md"] == digest(content)
    with pytest.raises(FileExistsError):
        evidence.write_bytes("artifacts/spec.md", b"replacement")
    assert evidence.path("artifacts/spec.md").read_bytes() == content
    evidence.write_json("repairs/r1.json", {"summary": "修复"})
    assert evidence.path("repairs/r1.json").read_bytes() == '{\n  "summary": "修复"\n}\n'.encode()
    with pytest.raises(BenchError, match="escapes run"):
        evidence.write_text("../escaped.txt", "bad")
    evidence.check()


@pytest.mark.parametrize("filename,version", [("repository-state.json", 3), ("exec-state.json", 1)])
def test_canonical_checkpoint_precedes_projection(tmp_path, filename, version):
    evidence = Evidence(tmp_path)
    evidence.write_text("artifacts/spec.md", "original")
    calls = []

    def projected(result, stage):
        saved = json.loads((tmp_path / filename).read_bytes())
        assert saved["result"] == result and saved["stage"] == stage
        assert saved["schema_version"] == version
        assert saved["evidence_sha256"] == evidence.known
        calls.append(stage)

    base = SimpleNamespace(path=tmp_path, checkpoint=projected)
    store = CheckpointStore(base, evidence, filename=filename, schema_version=version)
    store.checkpoint({"status": "RUNNING"}, "audit:prepared")
    assert calls == ["audit:prepared"]


@pytest.mark.parametrize("discard_clean", [False, True])
def test_recovery_counts_started_audit_once_and_preserves_pending_fixes(tmp_path, discard_clean):
    evidence = Evidence(tmp_path)
    evidence.write_text("raw/audit-01/stdout.txt", "partial")
    store = SimpleNamespace(path=tmp_path, evidence=evidence)
    active = {"audit_id": "a1", "status": "RUNNING"}
    result = {
        "audit_rounds": 0,
        "trajectory": [],
        "flow": {
            "last_call": {"role": "audit", "stage": "audit-01", "counter_before": 0},
            "active_audit": active,
            "pending_fix": {"findings": ["F1"]},
            "clean": ["old"],
        },
    }
    for _ in range(2):
        reconcile_invocation(store, result, {"started": True}, discard_clean=discard_clean)
    assert result["audit_rounds"] == 1
    assert result["trajectory"] == [{"audit_id": "a1", "status": "abandoned"}]
    assert result["flow"]["pending_fix"] == {"findings": ["F1"]}
    assert result["flow"]["clean"] == []


def test_exec_recovery_preserves_completed_first_clean_and_unstarted_call_is_not_counted(tmp_path):
    result = {
        "repair_rounds": 0,
        "flow": {
            "last_call": {"role": "repair", "stage": "repair-01", "counter_before": 0},
            "active_audit": None,
            "clean": ["completed-clean"],
        },
    }
    reconcile_invocation(
        SimpleNamespace(path=tmp_path, evidence=Evidence(tmp_path)), result, {"started": False}
    )
    assert result["repair_rounds"] == 0
    assert result["flow"]["clean"] == ["completed-clean"]


@pytest.mark.parametrize(
    "lock,message", [(session_lock, "still active"), (run_lock, "Run is locked")]
)
def test_locks_reject_a_second_writer_and_release_after_errors(tmp_path, lock, message):
    with pytest.raises(RuntimeError, match="failure"), lock(tmp_path):
        with pytest.raises(BenchError, match=message), lock(tmp_path):
            pytest.fail("Second writer entered")
        raise RuntimeError("failure")
    with lock(tmp_path):
        pass


@pytest.mark.parametrize("lock", [session_lock, run_lock])
def test_lock_body_resource_errors_are_not_reclassified(tmp_path, lock):
    error = BusyError("Other resource is busy")
    with pytest.raises(BusyError) as caught, lock(tmp_path):
        raise error
    assert caught.value is error


def test_kernel_lock_releases_on_process_exit_but_legacy_marker_is_retained(tmp_path):
    for name in ("session_lock", "run_lock"):
        code = (
            "from pathlib import Path; from mracbench.session import " + name + "; "
            "import os,sys; context=" + name + "(Path(sys.argv[1])); "
            "context.__enter__(); os._exit(0)"
        )
        subprocess.run([sys.executable, "-c", code, str(tmp_path)], check=True, timeout=20)
    with session_lock(tmp_path):
        pass
    with pytest.raises(BenchError, match="Run is locked"), run_lock(tmp_path):
        pytest.fail("Legacy crash marker was ignored")
