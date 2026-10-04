import json
from dataclasses import replace

import pytest
import yaml
from test_trial import SpecFlowAgent

from mracbench.models import BenchError
from mracbench.repository_flow import resume_repository_run
from mracbench.runner import run_case


@pytest.fixture(autouse=True)
def use_current_protocols(current_project):
    return current_project


def audit(request, severity=None):
    data = json.loads(request.prompt.split("INPUT JSON:\n", 1)[1])
    return json.dumps(
        {
            "audit_id": data["audit_id"],
            "findings": []
            if severity is None
            else [
                {
                    "severity": severity,
                    "title": "Missing result",
                    "evidence": "Spec §1 leaves result open",
                }
            ],
        }
    )


def repair(request):
    data = json.loads(request.prompt.split("INPUT JSON:\n", 1)[1])
    target = request.workspace / data["spec_path"]
    target.write_bytes(target.read_bytes() + b"\nThe failure result is null.\n")
    return "Updated Spec"


def test_default_eight_audit_cap_stops_before_last_repair_and_cannot_resume(config):
    config = replace(config, protocol_id="spec-mrac-v2")

    def alternating(request):
        number = int(request.raw_dir.name.rsplit("-", 1)[1])
        return audit(request, "P2" if number % 2 or number == 8 else None)

    replies = []
    for number in range(1, 9):
        replies.append(alternating)
        if number % 2:
            replies.append(repair)
    agent = SpecFlowAgent(replies)
    path, result = run_case(config, agent)
    assert result["status"] == "NON_CONVERGED", result["error"]
    assert result["audit_rounds"] == 8
    assert result["repair_rounds"] == 4
    assert result["protocol_version"] == 5
    assert result["trajectory"][-1]["repair_artifact"] is None
    assert result["flow"]["phase"] == "FIX"
    assert result["flow"]["pending_fix"]["findings"][0]["severity"] == "P2"
    assert not (path / "raw/repair-05").exists()
    assert not (path / "raw/audit-09").exists()
    metadata = yaml.safe_load((path / "run.yaml").read_bytes())
    assert metadata["effective_config"]["max_audit_rounds"] == 8
    empty = SpecFlowAgent([])
    with pytest.raises(BenchError, match="Cannot resume NON_CONVERGED"):
        resume_repository_run(path, empty)
    assert not empty.requests


def test_two_clean_audits_at_rounds_seven_and_eight_still_converge(config):
    config = replace(config, protocol_id="spec-mrac-v2")

    def p3(request):
        return audit(request, "P3")

    replies = [call for _ in range(6) for call in (p3, repair)] + [audit, audit]
    path, result = run_case(config, SpecFlowAgent(replies))
    assert result["status"] == "CONVERGED", result["error"]
    assert result["audit_rounds"] == result["convergence_round"] == 8
    assert not (path / "raw/audit-09").exists()


def test_saved_unlimited_budget_is_not_replaced_by_new_default(project, config):
    descriptor = project / "protocols/spec-mrac-v2/protocol.yaml"
    data = yaml.safe_load(descriptor.read_bytes())
    data["version"] = 4
    data["limits"]["max_audit_rounds"] = None
    descriptor.write_text(yaml.safe_dump(data), encoding="utf-8")
    config = replace(config, protocol_id="spec-mrac-v2")

    def finding(request):
        return audit(request, "P2")

    needs_input = json.dumps(
        {
            "disposition": "needs_input",
            "reason": "An external result is missing",
            "questions": ["Should failure return null?"],
        }
    )
    path, result = run_case(config, SpecFlowAgent([finding, needs_input]))
    assert result["status"] == "NEEDS_INPUT", result["error"]
    data["version"] = 5
    data["limits"]["max_audit_rounds"] = 8
    descriptor.write_text(yaml.safe_dump(data), encoding="utf-8")
    answer = project / "answer.md"
    answer.write_text("Failure returns null.", encoding="utf-8")
    _, resumed = resume_repository_run(path, SpecFlowAgent([repair, audit, audit]), answer)
    assert resumed["status"] == "CONVERGED", resumed["error"]
    metadata = yaml.safe_load((path / "run.yaml").read_bytes())
    assert metadata["effective_config"]["max_audit_rounds"] is None
    assert resumed["protocol_version"] == 4


def test_legacy_v4_finishes_last_repair(project, config):
    descriptor = project / "protocols/spec-mrac-v2/protocol.yaml"
    data = yaml.safe_load(descriptor.read_bytes())
    data["version"] = 4
    descriptor.write_text(yaml.safe_dump(data), encoding="utf-8")
    config = replace(config, protocol_id="spec-mrac-v2", max_rounds=2)

    def finding(request):
        return audit(request, "P2")

    _, result = run_case(config, SpecFlowAgent([audit, finding, repair]))
    assert result["status"] == "NON_CONVERGED", result["error"]
    assert result["protocol_version"] == 4
    assert result["audit_rounds"] == 2 and result["repair_rounds"] == 1
    assert result["flow"]["pending_fix"] is None
