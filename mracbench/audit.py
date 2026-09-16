import json
from dataclasses import dataclass

from .models import BenchError


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_audit(text: str) -> dict:
    try:
        data = json.loads(
            text,
            object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")),
        )
        if not isinstance(data, dict) or set(data) != {"status", "issues"}:
            raise ValueError("Expected exactly status and issues")
        if data["status"] not in ("clean", "issues_found") or not isinstance(data["issues"], list):
            raise ValueError("Invalid audit status/issues")
        seen = set()
        fields = {"id", "severity", "title", "description", "evidence", "required_change"}
        for issue in data["issues"]:
            if not isinstance(issue, dict) or set(issue) != fields:
                raise ValueError("Invalid issue fields")
            if any(not isinstance(v, str) or not v.strip() for v in issue.values()):
                raise ValueError("Issue fields must be nonempty strings")
            if issue["severity"] not in ("blocking", "non_blocking"):
                raise ValueError("Invalid severity")
            if issue["id"] in seen:
                raise ValueError("Duplicate issue id")
            seen.add(issue["id"])
        expected = "issues_found" if blocking_count(data) else "clean"
        if data["status"] != expected:
            raise ValueError("Status disagrees with blocking issues")
        return data
    except (ValueError, TypeError, RecursionError) as exc:
        raise BenchError("PARSE_ERROR", f"Invalid audit JSON: {exc}") from exc


def blocking_count(audit: dict) -> int:
    return sum(issue["severity"] == "blocking" for issue in audit["issues"])


def parse_spec(text: str) -> str:
    body = text.strip()
    if not body.startswith("# ") or len(body.splitlines()) < 2:
        raise BenchError(
            "PARSE_ERROR", "Spec must be a complete Markdown document starting with '# '"
        )
    return body + "\n"


@dataclass
class Convergence:
    clean_streak: int = 0

    def observe(self, blocking: int) -> bool:
        self.clean_streak = self.clean_streak + 1 if blocking == 0 else 0
        return self.clean_streak >= 2
