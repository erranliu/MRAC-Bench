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
    assert candidate["windows_sandbox"] == "unelevated"
    tested = candidate["trial"]()
    assert tested["state"] == "REVIEW_PENDING"
    assert tested["windows_sandbox"] == "unelevated"
    assert tested["statistics"]["overall"]["run_count"] == 2
    assert tested["statistics"]["overall"]["converged_runs"] == 0
    assert [trial["model"] for trial in tested["trials"]] == ["gpt-5.6-luna", "gpt-6.1-sol"]
    assert len(list((candidate["path"] / "workspaces").iterdir())) == 2
    for trial in tested["trials"]:
        run_path = Path(trial["run_dir"])
        metadata = yaml.safe_load((run_path / "run.yaml").read_bytes())
        result = json.loads(Path(trial["result_file"]).read_bytes())
        assert metadata["effective_config"]["model"] == trial["model"]
        assert metadata["effective_config"]["windows_sandbox"] == "unelevated"
        assert trial["windows_sandbox"] == "unelevated"
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
    assert retested["statistics"]["overall"]["run_count"] == 1
    metadata = yaml.safe_load((Path(retested["trials"][0]["run_dir"]) / "run.yaml").read_bytes())
    assert metadata["effective_config"]["reasoning_effort"] == "high"
    assert metadata["effective_config"]["max_audit_rounds"] == 2
    assert metadata["effective_config"]["agent_timeout_seconds"] == 5
    assert metadata["effective_config"]["windows_sandbox"] == "unelevated"
    assert first.exists()


@pytest.mark.parametrize("saved_mode", [None, "elevated"])
def test_existing_candidates_retest_with_unelevated(candidate, saved_mode):
    metadata_path = candidate["path"] / "candidate.json"
    data = json.loads(metadata_path.read_bytes())
    if saved_mode is None:
        data.pop("windows_sandbox")
    else:
        data["windows_sandbox"] = saved_mode
    metadata_path.write_text(json.dumps(data), encoding="utf-8")
    tested = candidate["trial"]("--model", "gpt-5.6-luna")
    assert tested["windows_sandbox"] == "unelevated"
    run = Path(tested["trials"][0]["run_dir"])
    assert (
        yaml.safe_load((run / "run.yaml").read_bytes())["effective_config"]["windows_sandbox"]
        == "unelevated"
    )
    assert json.loads(metadata_path.read_bytes())["windows_sandbox"] == "unelevated"


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


def load_helper():
    spec = importlib.util.spec_from_file_location("pr_case_statistics", HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.checked = checked
    return helper


def test_invocation_averages_include_started_failures_and_simple_initial_audit(tmp_path):
    helper = load_helper()
    records = {
        "spec-init-01": {"started": True, "duration_seconds": 10},
        "spec-freeze-loop-02": {"started": True, "duration_seconds": 30, "error_type": "TIMEOUT"},
        "audit-03": {"started": False, "duration_seconds": 500},
        "repair-01": {"started": True, "duration_seconds": 25},
        "repair-02": {"started": True, "duration_seconds": None},
        "review-01": {"started": True, "duration_seconds": 900},
        "closure-01": {"started": True, "duration_seconds": 900},
        "repository-read-check-01": {"started": True, "duration_seconds": 900},
    }
    for stage, record in records.items():
        path = tmp_path / "raw" / stage
        path.mkdir(parents=True)
        (path / "execution.json").write_text(json.dumps(record), encoding="utf-8")
    timings = helper.invocation_timings(tmp_path, {"audit_rounds": 2, "repair_rounds": 2})
    assert timings["audit"]["average_seconds"] == 20
    assert timings["audit"]["timed_calls"] == 2
    assert timings["repair"]["average_seconds"] == 25
    assert timings["repair"]["timed_calls"] == 1
    assert timings["repair"]["calls"] == 2


def test_statistics_include_failed_runs_and_do_not_invent_missing_samples():
    helper = load_helper()
    trials = [
        {
            "status": "CONVERGED",
            "wall_time_seconds": 10,
            "audit_rounds": 4,
            "repair_rounds": 1,
            "timings": {"audit": {"calls": 4, "timed_calls": 2, "timed_total_seconds": 20}},
        },
        {
            "status": "NON_CONVERGED",
            "wall_time_seconds": 30,
            "audit_rounds": 8,
            "repair_rounds": 2,
            "timings": {"audit": {"calls": 8, "timed_calls": 1, "timed_total_seconds": 25}},
        },
        {"status": "ERROR", "wall_time_seconds": None},
    ]
    statistics = helper.basic_statistics(trials)
    assert statistics["run_count"] == 3 and statistics["converged_runs"] == 1
    assert statistics["status_counts"] == {"CONVERGED": 1, "NON_CONVERGED": 1, "ERROR": 1}
    assert statistics["average_wall_time_seconds"] == 20
    assert statistics["wall_time_seconds_samples"] == 2
    assert statistics["total_wall_time_seconds"] is None
    assert statistics["average_audit_rounds"] == 6
    assert statistics["average_repair_rounds"] == 1.5
    assert statistics["timings"]["audit"]["average_seconds"] == 15
    assert statistics["timings"]["audit"]["timed_calls"] == 3
    assert statistics["timings"]["audit"]["calls"] is None
    assert statistics["timings"]["repair"]["average_seconds"] is None
    complete = helper.basic_statistics(trials[:2])
    assert complete["total_wall_time_seconds"] == 40
    assert complete["timings"]["audit"]["calls"] == 12
    assert helper.basic_statistics([])["average_wall_time_seconds"] is None
