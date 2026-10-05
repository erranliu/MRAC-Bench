"""The project skill uses local Git and an unavailable CLI, never live models."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from conftest import PROJECT, git

from mrac_resources.cases import Registry
from mrac_resources.home import checked
from mrac_resources.locks import file_lock

HELPER = PROJECT / ".agents/skills/pr-case/scripts/pr_case.py"


def json_values(output):
    decoder = json.JSONDecoder()
    result = []
    while output.strip():
        output = output.lstrip()
        value, length = decoder.raw_decode(output)
        result.append(value)
        output = output[length:]
    return result


@pytest.fixture
def candidate(tmp_path):
    home = tmp_path / "bench-home"
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    git(repo, "add", "app.py")
    git(
        repo,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "base",
    )
    commit = git(repo, "rev-parse", "HEAD")

    def call(*args, code=0, cwd=PROJECT):
        process = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(HELPER),
                "--bench-root",
                str(PROJECT),
                "--bench-home",
                str(home),
                *args,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            cwd=cwd,
            check=False,
        )
        assert process.returncode == code, process.stdout + process.stderr
        return json_values(process.stdout)[-1]

    created = call("create", "--pr-url", "https://github.com/example/project/pull/123")
    path = Path(created["path"])
    spec = Path(created["spec_file"])
    spec.write_bytes(b"\xef\xbb\xbf# Spec\r\nReturn two instead of one.\r\n")
    metadata = json.loads((path / "candidate.json").read_bytes())
    metadata["repository_url"] = str(repo)
    (path / "candidate.json").write_text(json.dumps(metadata), encoding="utf-8")

    def trial(*args):
        return call(
            "test",
            "--candidate",
            created["candidate"],
            "--commit",
            commit,
            "--codex-executable",
            "nonexistent-pr-case-test-cli",
            *args,
        )

    yield {**created, "home": home, "path": path, "spec": spec, "call": call, "trial": trial}
    call("clean", "--all")


def test_default_trials_preserve_input_and_save_only_package(candidate):
    original = candidate["spec"].read_bytes()
    tested = candidate["trial"]()
    assert tested["state"] == "REVIEW_PENDING"
    assert [trial["model"] for trial in tested["trials"]] == ["gpt-5.6-luna", "gpt-6.1-sol"]
    assert len(list((candidate["path"] / "workspaces").iterdir())) == 2
    for trial in tested["trials"]:
        run_path = Path(trial["run_dir"])
        metadata = yaml.safe_load((run_path / "run.yaml").read_bytes())
        result = json.loads(Path(trial["result_file"]).read_bytes())
        assert metadata["effective_config"]["model"] == trial["model"]
        assert metadata["protocol_id"] == "spec-mrac-v2"
        assert trial["status"] != "CONVERGED"
        assert trial["audit_rounds"] == result["audit_rounds"]
        assert trial["repair_rounds"] == result["repair_rounds"]
        assert trial["wall_time_seconds"] == result["usage"]["wall_time_seconds"]
        assert trial["error"] == result["error"]
        assert trial["protocol_version"] == result["protocol_version"]
        if trial["final_artifact"]:
            assert Path(trial["final_artifact"]).is_absolute()
            assert Path(trial["final_artifact"]).read_bytes() == original
            assert trial["spec_changed"] is False
    saved = candidate["call"]("save", "--candidate", candidate["candidate"], "--accept")
    assert saved["case_key"] == "C000001"
    assert not candidate["path"].exists()
    package = Path(saved["path"])
    assert {entry.name for entry in package.iterdir()} == {"case.yaml", "spec.md"}
    assert (package / "spec.md").read_bytes() == original
    case = yaml.safe_load((package / "case.yaml").read_bytes())
    assert case["source"]["upstream_url"] == "https://github.com/example/project/pull/123"
    registry = Registry(candidate["home"])
    try:
        assert registry.resolve(saved["case_key"])["path"] == str(package)
    finally:
        registry.close()
    candidate["call"]("clean", "--all")
    assert package.exists()


def test_overrides_replace_defaults_and_retest_keeps_configuration(candidate):
    tested = candidate["trial"](
        "--model",
        "gpt-6.1-sol",
        "--protocol",
        "spec-flow-simple-v1",
        "--reasoning-effort",
        "high",
        "--max-rounds",
        "2",
        "--timeout",
        "5",
    )
    assert len(tested["trials"]) == 1
    first = Path(tested["trials"][0]["run_dir"])
    candidate["spec"].write_text("# Revised Spec\nReturn three.\n", encoding="utf-8")
    retested = candidate["trial"]()
    assert retested["models"] == ["gpt-6.1-sol"]
    assert retested["protocol"] == "spec-flow-simple-v1"
    metadata = yaml.safe_load((Path(retested["trials"][0]["run_dir"]) / "run.yaml").read_bytes())
    assert metadata["effective_config"]["reasoning_effort"] == "high"
    assert metadata["effective_config"]["max_audit_rounds"] == 2
    assert metadata["effective_config"]["agent_timeout_seconds"] == 5
    assert first.exists()


def test_save_requires_test_and_rejects_input_changed_after_test(candidate):
    candidate["call"]("save", "--candidate", candidate["candidate"], "--accept", code=2)
    candidate["trial"]()
    candidate["spec"].write_text("Changed after test", encoding="utf-8")
    failure = candidate["call"]("save", "--candidate", candidate["candidate"], "--accept", code=2)
    assert "retest" in failure["error"]
    assert candidate["path"].exists()
    assert not (candidate["home"] / "cases/registry.sqlite").exists()


def test_cleanup_retains_active_process_and_refuses_outside_path(candidate, tmp_path):
    raw = candidate["path"] / "runs/fake/raw/audit-01"
    raw.mkdir(parents=True)
    invocation = raw / "invocation.json"
    invocation.write_text(
        json.dumps({"started": True, "pid": os.getpid(), "ended_at": None}), encoding="utf-8"
    )
    retained = candidate["call"]("clean", "--all")
    assert len(retained["retained"]) == 1
    outside = tmp_path / "keep.txt"
    outside.write_text("keep", encoding="utf-8")
    rejected = candidate["call"]("clean", "--candidate", "../keep.txt")
    assert len(rejected["retained"]) == 1
    assert outside.read_text(encoding="utf-8") == "keep"
    invocation.unlink()
    with file_lock(candidate["home"] / "pr-case-scratch/.activity.lock", blocking=False):
        candidate["call"]("clean", "--all", code=2)
    assert candidate["path"].exists()
    candidate["call"]("clean", "--candidate", candidate["candidate"])
    assert not candidate["path"].exists()


def test_helper_discovers_repository_from_its_location(tmp_path):
    process = subprocess.run(
        [sys.executable, "-X", "utf8", str(HELPER), "--bench-home", str(tmp_path / "home"), "list"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        env={key: value for key, value in os.environ.items() if key != "PR_CASE_BENCH_ROOT"},
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    assert json.loads(process.stdout) == []


def test_summary_exposes_pending_questions_and_distinguishes_code_patch(tmp_path):
    module_spec = importlib.util.spec_from_file_location("pr_case_helper", HELPER)
    helper = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(helper)
    helper.checked = checked
    artifact = tmp_path / "artifacts/spec.md"
    artifact.parent.mkdir()
    artifact.write_bytes(b"Revised Spec")
    result = {
        "run_id": "trial-1",
        "status": "NEEDS_INPUT",
        "final_artifact": "artifacts/spec.md",
        "terminal_reason": "Missing decision",
        "flow": {"questions": ["What is the failure behavior?"]},
    }
    spec_summary = helper.trial_summary(
        "test-model", tmp_path, result, b"Input", "repository-spec-freeze"
    )
    assert spec_summary["spec_changed"] is True
    assert spec_summary["final_artifact"] == str(artifact)
    assert spec_summary["questions"] == ["What is the failure behavior?"]
    assert spec_summary["terminal_reason"] == "Missing decision"
    assert spec_summary["audit_rounds"] is None
    code_summary = helper.trial_summary("test-model", tmp_path, result, b"Input", "exec-mrac")
    assert code_summary["artifact_type"] == "code_patch"
    assert code_summary["spec_changed"] is None
    artifact.unlink()
    missing_summary = helper.trial_summary(
        "test-model", tmp_path, result, b"Input", "repository-spec-freeze"
    )
    assert missing_summary["final_artifact"] is None
    assert missing_summary["spec_changed"] is None
