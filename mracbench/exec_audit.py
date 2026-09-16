"""Strict exec-mrac outputs: every finding prevents a clean audit."""

from .models import BenchError
from .simple_audit import decode, nonempty, obj


def parse_exec_audit(text, audit_id, spec_sha256, candidate_sha256):
    try:
        data = decode(text)
        obj(data, {"audit_id", "spec_sha256", "candidate_sha256", "findings"})
        if (data["audit_id"], data["spec_sha256"], data["candidate_sha256"]) != (
            audit_id,
            spec_sha256,
            candidate_sha256,
        ):
            raise ValueError("Audit identity/Spec/candidate boundary mismatch")
        if not isinstance(data["findings"], list):
            raise TypeError("Expected findings array")
        for finding in data["findings"]:
            obj(finding, {"severity", "title", "evidence"})
            for value in finding.values():
                nonempty(value)
            if finding["severity"] not in {"P0", "P1", "P2", "P3"}:
                raise ValueError("Unknown severity")
        return data
    except (ValueError, TypeError, RecursionError) as exc:
        raise BenchError("AUDIT_INVALID", str(exc)) from exc


def parse_code_result(text, findings=None):
    try:
        data = decode(text)
        obj(data, {"disposition", "summary", "validation"}, {"fixes", "questions"})
        nonempty(data["summary"])
        if not isinstance(data["validation"], list) or not data["validation"]:
            raise ValueError("Record verification or an explicit not_run reason")
        for check in data["validation"]:
            obj(check, {"command", "status", "evidence"})
            for value in check.values():
                nonempty(value)
            if check["status"] not in {"passed", "failed", "not_run"}:
                raise ValueError("Unknown verification status")
        if data["disposition"] == "needs_input":
            if (
                "fixes" in data
                or not isinstance(data.get("questions"), list)
                or not data["questions"]
            ):
                raise ValueError(
                    "Missing input requires questions and must not claim resolved fixes"
                )
            for question in data["questions"]:
                nonempty(question)
        elif data["disposition"] == "complete":
            if "questions" in data:
                raise ValueError("Complete result cannot carry pending questions")
            if findings is None:
                if "fixes" in data:
                    raise ValueError("Initial implementation has no finding IDs")
            else:
                if not isinstance(data.get("fixes"), list):
                    raise ValueError("Repair requires closure evidence for every finding")
                seen = set()
                for fix in data["fixes"]:
                    obj(fix, {"finding_id", "summary", "evidence"})
                    for value in fix.values():
                        nonempty(value)
                    if fix["finding_id"] in seen:
                        raise ValueError("Duplicate fix")
                    seen.add(fix["finding_id"])
                if seen != {f["finding_id"] for f in findings}:
                    raise ValueError("Every reported finding must be addressed, including P3")
        else:
            raise ValueError("Expected complete or needs_input")
        return data
    except (ValueError, TypeError, RecursionError) as exc:
        raise BenchError("FIX_INVALID", str(exc)) from exc
