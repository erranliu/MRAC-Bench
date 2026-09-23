"""Strict schemas and decision policy for the Spec-init/freeze workflow."""

import json

from .audit import _unique, parse_spec
from .models import BenchError


def obj(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) <= set(required) | set(
        optional
    ):
        raise ValueError("Unexpected object fields")


def nonempty(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected a nonempty string")


def decode(text):
    return json.loads(
        text,
        object_pairs_hook=_unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")),
    )


def parse_findings(text, audit_id):
    try:
        data = decode(text)
        obj(data, {"audit_id", "findings"})
        if data["audit_id"] != audit_id or not isinstance(data["findings"], list):
            raise ValueError("Audit ID or findings mismatch")
        for finding in data["findings"]:
            obj(finding, {"severity", "title", "evidence"})
            for value in finding.values():
                nonempty(value)
            if finding["severity"] not in {"P0", "P1", "P2", "P3"}:
                raise ValueError("Unknown severity")
        return data
    except (ValueError, TypeError, RecursionError) as exc:
        raise BenchError("PARSE_ERROR", f"Invalid findings JSON: {exc}") from exc


def assign_ids(audit):
    return [{"finding_id": f"F{i}", **row} for i, row in enumerate(audit["findings"], 1)]


def parse_review(text, audit_id, findings):
    try:
        data = decode(text)
        obj(data, {"audit_id", "decisions"})
        if data["audit_id"] != audit_id or not isinstance(data["decisions"], list):
            raise ValueError("Audit ID or decisions mismatch")
        by_id = {f["finding_id"]: f for f in findings}
        seen = set()
        accepted, deferred = [], []
        for decision in data["decisions"]:
            obj(decision, {"finding_id", "outcome"}, {"reason"})
            fid, outcome = decision["finding_id"], decision["outcome"]
            nonempty(fid)
            nonempty(outcome)
            if fid not in by_id or fid in seen:
                raise ValueError("Unknown or duplicate finding ID")
            seen.add(fid)
            if outcome not in {"accepted", "rejected", "deferred"}:
                raise ValueError("Unknown decision")
            if outcome == "accepted":
                if "reason" in decision:
                    raise ValueError("Only rejections carry reason")
                accepted.append(by_id[fid])
            else:
                if outcome == "rejected":
                    reason = decision.get("reason")
                    nonempty(reason)
                    if len(reason) > 500 or reason != " ".join(reason.split()):
                        raise ValueError("Rejection needs one concise evidence-based reason")
                else:
                    if by_id[fid]["severity"] != "P3" or "reason" in decision:
                        raise ValueError("Only P3 can be deferred; deferred has no extra fields")
                    deferred.append(by_id[fid])
        if seen != set(by_id):
            raise ValueError("Every finding needs exactly one decision")
        return data, accepted, deferred
    except (ValueError, TypeError, RecursionError) as exc:
        raise BenchError("PARSE_ERROR", f"Invalid review JSON: {exc}") from exc


def parse_repair(text, audit_id, accepted):
    try:
        data = decode(text)
        if not isinstance(data, dict):
            raise TypeError("Expected repair object")
        if data.get("disposition") == "continue":
            obj(data, {"audit_id", "disposition", "spec", "fixes"})
            parse_spec(data["spec"])
            if not isinstance(data["fixes"], list):
                raise ValueError("Expected fixes list")
            seen = set()
            for fix in data["fixes"]:
                obj(fix, {"finding_id", "summary", "evidence"})
                for value in fix.values():
                    nonempty(value)
                if fix["finding_id"] in seen:
                    raise ValueError("Duplicate fix")
                seen.add(fix["finding_id"])
            if seen != {row["finding_id"] for row in accepted}:
                raise ValueError("Every accepted finding needs a closure record")
        else:
            raise ValueError("Repair disposition must be continue")
        if data["audit_id"] != audit_id:
            raise ValueError("Audit ID mismatch")
        return data
    except (ValueError, TypeError, AttributeError, RecursionError) as exc:
        raise BenchError("PARSE_ERROR", f"Invalid repair JSON: {exc}") from exc
