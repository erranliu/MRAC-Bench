import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from conftest import StubAgent as BaseStubAgent

from mracbench.models import AgentResult
from mracbench.repository_flow import resume_repository_run
from mracbench.repository_mcp import RepositoryReader
from mracbench.runner import run_case
from mracbench.simple_flow import resume_run


def inputs(request):
    # Simple @14+ appends the literal immutable source after the JSON input object.
    return json.JSONDecoder().raw_decode(request.prompt.split("INPUT JSON:\n", 1)[1])[0]


class CurrentAgent(BaseStubAgent):
    def run(self, request):
        if request.raw_dir.name.startswith("closure-"):
            self.requests.append(request)
            assert request.mcp_servers is None
            return AgentResult(final_text="CLOSED", started=True, exit_code=0)
        return super().run(request)


def simple_audit(severity=None):
    def reply(request):
        data = inputs(request)
        assert "audit_id" not in data and "spec_sha256" not in data
        return json.dumps(
            {
                "findings": []
                if severity is None
                else [
                    {
                        "severity": severity,
                        "title": "Failure behavior",
                        "evidence": "Specify null.",
                    }
                ]
            }
        )

    return reply


def review(*, reject=False):
    def reply(request):
        data = inputs(request)
        assert "audit_id" not in data
        return json.dumps(
            {
                "decisions": [
                    {
                        "finding_id": f["finding_id"],
                        "outcome": "rejected" if reject else "accepted",
                        **(
                            {"reason": ("Source acceptance already defines null. " * 20).strip()}
                            if reject
                            else {}
                        ),
                    }
                    for f in data["findings"]
                ]
            }
        )

    return reply


def repair(request):
    data = inputs(request)
    path = request.workspace / data["spec_path"]
    path.write_bytes(path.read_bytes() + b"\nFailure returns null.\n")
    assert not request.readonly
    return "Updated Spec."


def repository_audit(severity=None):
    def reply(request):
        args = request.mcp_servers["mrac_repository"]["args"]
        values = {
            name: args[args.index(name) + 1]
            for name in (
                "--repository",
                "--head",
                "--manifest",
                "--audit-log",
            )
        }
        reader = RepositoryReader(
            Path(values["--repository"]),
            values["--head"],
            Path(values["--manifest"]),
            Path(values["--audit-log"]),
        )
        for tool, arguments in [("repository_head", {}), ("repository_read", {"path": "app.py"})]:
            reader.record(tool, arguments, result=reader.call(tool, arguments))
        return json.dumps(
            {
                "audit_id": inputs(request)["audit_id"],
                "findings": [
                    {
                        "severity": severity,
                        "title": "Failure behavior",
                        "evidence": "Specify null.",
                    },
                ]
                if severity
                else [],
            }
        )

    return reply


@pytest.mark.parametrize("version", [12, 13, 14, 15])
def test_simple_checkout_repair_and_fresh_freeze_audits(current_project, config, version):
    protocol_file = current_project / "protocols/spec-flow-simple-v1/protocol.yaml"
    protocol = yaml.safe_load(protocol_file.read_bytes())
    protocol["version"] = version
    protocol_file.write_text(yaml.safe_dump(protocol), encoding="utf-8")
    agent = CurrentAgent(
        [
            simple_audit("P3"),
            review(),
            repair,
            simple_audit(),
            review(),
            simple_audit(),
            review(),
        ]
    )
    path, result = run_case(replace(config, protocol_id="spec-flow-simple-v1"), agent)
    assert result["status"] == "CONVERGED", result["error"]
    assert result["protocol_version"] == version
    assert result["repair_rounds"] == 1 and result["closure_rounds"] == 1
    assert [row["clean_streak"] for row in result["trajectory"]] == [0, 1, 2]
    candidate = path / "checkout/SPEC.md"
    assert candidate.read_bytes() == (path / result["final_artifact"]).read_bytes()
    # The execution snapshot records the revision used for this run.
    assert (
        json.loads((path / "input/execution-config.json").read_bytes())["protocol_version"]
        == version
    )


@pytest.mark.parametrize("version,status", [(14, "PARSE_ERROR"), (15, "CONVERGED")])
def test_simple_review_reason_limit_is_selected_by_revision(
    current_project, config, version, status
):
    protocol_file = current_project / "protocols/spec-flow-simple-v1/protocol.yaml"
    protocol = yaml.safe_load(protocol_file.read_bytes())
    protocol["version"] = version
    protocol_file.write_text(yaml.safe_dump(protocol), encoding="utf-8")
    agent = CurrentAgent([simple_audit("P1"), review(reject=True)] + [simple_audit(), review()] * 2)
    _, result = run_case(replace(config, protocol_id="spec-flow-simple-v1"), agent)
    assert result["status"] == status, result["error"]


def test_simple_current_pause_and_resume_keep_saved_policy(current_project, config):
    replies = [simple_audit(), review()]
    for index in range(6):
        replies += [simple_audit("P1"), review()]
        if index < 5:
            replies.append(repair)
    path, result = run_case(
        replace(config, protocol_id="spec-flow-simple-v1", max_rounds=12), CurrentAgent(replies)
    )
    assert result["status"] == "PAUSED", result["error"]
    assert result["repair_rounds"] == 5 and result["flow"]["pending_fix"]
    # Changing project YAML cannot change the saved @15 continuation.
    (current_project / "protocols/spec-flow-simple-v1/protocol.yaml").write_text("invalid")
    _, resumed = resume_run(path, CurrentAgent([repair] + [simple_audit(), review()] * 2))
    assert resumed["status"] == "CONVERGED", resumed["error"]
    assert resumed["protocol_version"] == 15 and resumed["repair_rounds"] == 6


def test_repository_current_pause_after_six_repairs_and_resume(current_project, config):
    path, result = run_case(
        replace(config, protocol_id="spec-mrac-v2"),
        CurrentAgent([repository_audit("P1"), repair] * 6),
    )
    assert result["status"] == "PAUSED", result["error"]
    assert result["protocol_version"] == 5 and result["repair_rounds"] == 6
    assert result["flow"]["pending_fix"] is None
    canonical = json.loads((path / "repository-state.json").read_bytes())
    assert canonical["schema_version"] == 3 and canonical["result"] == result
    (current_project / "protocols/spec-mrac-v2/protocol.yaml").write_text("invalid")
    _, resumed = resume_repository_run(path, CurrentAgent([repository_audit()] * 2))
    assert resumed["status"] == "CONVERGED", resumed["error"]
    assert resumed["audit_rounds"] == 8 and resumed["repair_rounds"] == 6
