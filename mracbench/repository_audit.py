"""MRAC-Spec v2 schemas: repository evidence is required even for clean audits."""

from pathlib import PurePosixPath

from .models import BenchError
from .repository import git
from .simple_audit import EXCEPTIONS, decode, nonempty, obj


def parse_audit(text, audit_id, repo):
    try:
        data = decode(text)
        obj(data, {"audit_id", "repository_review", "findings"})
        if data["audit_id"] != audit_id or not isinstance(data["findings"], list):
            raise ValueError("Audit ID or findings mismatch")
        review = data["repository_review"]
        obj(review, {"base_head", "checks"})
        if review["base_head"] != repo.commit:
            raise ValueError("Repository baseline mismatch")
        if not isinstance(review["checks"], list) or not review["checks"]:
            raise ValueError("Repository evidence is required, including for clean")
        for check in review["checks"]:
            obj(check, {"path", "symbol", "conclusion"})
            for value in check.values():
                nonempty(value)
            name = check["path"]
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
                raise ValueError("Evidence needs a repository-relative file path")
            if git(repo.path, "cat-file", "-t", f"{repo.commit}:{name}") != "blob":
                raise ValueError("Evidence must cite a tracked file at the fixed commit")
        for finding in data["findings"]:
            obj(finding, {"severity", "title", "evidence"})
            for value in finding.values():
                nonempty(value)
            if finding["severity"] not in {"P0", "P1", "P2", "P3"}:
                raise ValueError("Unknown severity")
        return data
    except (ValueError, TypeError, RecursionError, BenchError) as exc:
        raise BenchError("AUDIT_INVALID", f"Invalid repository audit: {exc}") from exc


def parse_review(text, audit_id, findings):
    try:
        data = decode(text)
        # The parent in the source skill checks semantic relevance. This field makes
        # that supervision explicit in the automated adapter, including empty findings.
        obj(data, {"audit_id", "repository_assessment", "decisions"})
        if data["audit_id"] != audit_id or not isinstance(data["decisions"], list):
            raise ValueError("Audit ID or decisions mismatch")
        assessment = data["repository_assessment"]
        obj(assessment, {"status", "reason"})
        nonempty(assessment["reason"])
        if assessment["status"] not in ("supported", "insufficient"):
            raise ValueError("Unknown repository assessment")
        by_id = {f["finding_id"]: f for f in findings}
        seen, accepted, deferred = set(), [], []
        for decision in data["decisions"]:
            obj(decision, {"finding_id", "outcome"}, {"exception", "reason"})
            fid, outcome = decision["finding_id"], decision["outcome"]
            nonempty(fid)
            nonempty(outcome)
            if fid not in by_id or fid in seen:
                raise ValueError("Unknown or duplicate finding")
            seen.add(fid)
            if outcome == "accepted":
                if "reason" in decision:
                    raise ValueError("Accepted decisions do not carry reason")
                finding = dict(by_id[fid])
                if "exception" in decision:
                    nonempty(decision["exception"])
                    if decision["exception"] not in EXCEPTIONS:
                        raise ValueError("Unknown exception category")
                    finding["exception"] = decision["exception"]
                accepted.append(finding)
            elif outcome in ("rejected", "deferred"):
                nonempty(decision.get("reason"))
                if "exception" in decision:
                    raise ValueError("Only accepted findings carry exception metadata")
                if outcome == "deferred":
                    if by_id[fid]["severity"] != "P3":
                        raise ValueError("Only P3 can be deferred")
                    deferred.append({**by_id[fid], "reason": decision["reason"]})
            else:
                raise ValueError("Unknown decision")
        if seen != set(by_id):
            raise ValueError("Every finding needs exactly one decision")
        return data, accepted, deferred
    except (ValueError, TypeError, RecursionError) as exc:
        raise BenchError("REVIEW_INVALID", str(exc)) from exc


def parse_repair(text, audit_id, accepted):
    try:
        data = decode(text)
        if not isinstance(data, dict) or data.get("audit_id") != audit_id:
            raise ValueError("Repair audit ID mismatch")
        if data.get("disposition") == "needs_input":
            obj(data, {"audit_id", "disposition", "reason", "questions"})
            nonempty(data["reason"])
            if not isinstance(data["questions"], list) or not data["questions"]:
                raise ValueError("Missing input requires concrete questions")
            for question in data["questions"]:
                nonempty(question)
        elif data.get("disposition") == "continue":
            obj(data, {"audit_id", "disposition", "spec", "fixes"})
            nonempty(data["spec"])
            if not isinstance(data["fixes"], list):
                raise ValueError("Expected fixes")
            seen = set()
            for fix in data["fixes"]:
                obj(fix, {"finding_id", "summary", "evidence"})
                for value in fix.values():
                    nonempty(value)
                if fix["finding_id"] in seen:
                    raise ValueError("Duplicate fix")
                seen.add(fix["finding_id"])
            if seen != {f["finding_id"] for f in accepted}:
                raise ValueError("Every accepted finding requires correction and evidence")
        else:
            raise ValueError("Expected continue or needs_input; there is no block disposition")
        return data
    except (ValueError, TypeError, RecursionError) as exc:
        raise BenchError("FIX_INVALID", str(exc)) from exc
