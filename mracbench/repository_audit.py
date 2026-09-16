"""Direct audit findings; optional legacy repository checklists remain readable."""

from pathlib import PurePosixPath

from .models import BenchError
from .repository import git
from .simple_audit import decode, nonempty, obj


def parse_audit(text, audit_id, repo):
    try:
        data = decode(text)
        obj(data, {"audit_id", "findings"}, {"repository_review"})
        if data["audit_id"] != audit_id or not isinstance(data["findings"], list):
            raise ValueError("Audit ID or findings mismatch")
        if "repository_review" in data:
            validate_repository_review(data["repository_review"], repo)
        for finding in data["findings"]:
            obj(finding, {"severity", "title", "evidence"})
            for value in finding.values():
                nonempty(value)
            if finding["severity"] not in {"P0", "P1", "P2", "P3"}:
                raise ValueError("Unknown severity")
        return data
    except (ValueError, TypeError, RecursionError, BenchError) as exc:
        raise BenchError("AUDIT_INVALID", f"Invalid repository audit: {exc}") from exc


def validate_repository_review(review, repo):
    """Validate only a supplied legacy checklist, never require coverage evidence."""
    obj(review, {"base_head", "checks"})
    if review["base_head"] != repo.commit:
        raise ValueError("Repository baseline mismatch")
    if not isinstance(review["checks"], list) or not review["checks"]:
        raise ValueError("Legacy repository checks must be nonempty")
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


def parse_repair(text, audit_id, findings):
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
            if seen != {f["finding_id"] for f in findings}:
                raise ValueError("Every audit finding requires correction and evidence")
        else:
            raise ValueError("Expected continue or needs_input; there is no block disposition")
        return data
    except (ValueError, TypeError, RecursionError) as exc:
        raise BenchError("FIX_INVALID", str(exc)) from exc
