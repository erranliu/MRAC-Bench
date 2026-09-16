"""Shared read-only invocation and evidence capture for both workflows."""

from .models import AgentRequest, BenchError
from .runs import write_json


class Invoker:
    def __init__(
        self, store, result, repo, adapter, model, timeout, effort=None, guard=None, capture=None
    ):
        self.store = store
        self.result = result
        self.repo = repo
        self.adapter = adapter
        self.model = model
        self.timeout = timeout
        self.effort = effort
        self.guard = guard
        self.capture = capture
        self.stage = "repository"

    def __call__(self, stage, prompt, audit_item=None, *, workspace=None):
        self.stage = stage
        store, result, repo = self.store, self.result, self.repo
        raw = store.path / "raw" / stage
        raw.mkdir(exist_ok=False)
        before = repo.inspect()
        write_json(raw / "repository-before.json", before)
        if before["violation"]:
            raise BenchError("PROTOCOL_VIOLATION", "Repository changed before invocation")
        if self.guard:
            self.guard()
        (raw / "request.txt").write_text(prompt, encoding="utf-8")
        store.checkpoint(result, stage + ":started")
        execution = self.adapter.run(
            AgentRequest(
                prompt,
                workspace or repo.path,
                raw,
                self.timeout,
                self.model,
                reasoning_effort=self.effort,
                skip_git_repo_check=workspace is not None,
            )
        )
        for filename, content in (
            ("stdout.txt", execution.stdout),
            ("stderr.txt", execution.stderr),
            ("final.txt", execution.final_text),
        ):
            if not (raw / filename).exists():
                (raw / filename).write_text(content, encoding="utf-8")
        write_json(
            raw / "execution.json",
            {
                "started": execution.started,
                "exit_code": execution.exit_code,
                "duration_seconds": execution.duration_seconds,
                "error_type": execution.error_type,
                "error_message": execution.error_message,
                "usage": execution.usage,
                "metadata": execution.metadata,
            },
        )
        if execution.started:
            if audit_item is not None:
                result["audit_rounds"] += 1
                result["trajectory"].append(audit_item)
            elif stage.startswith("repair-"):
                result["repair_rounds"] += 1
            elif stage.startswith("review-"):
                result["review_rounds"] += 1
        after = repo.inspect()
        write_json(raw / "repository-after.json", after)
        error = None
        if self.guard:
            try:
                self.guard()
            except BenchError as exc:
                error = exc
                write_json(raw / "evidence-violation.json", {"error": str(exc)})
        if after["violation"]:
            error = BenchError("PROTOCOL_VIOLATION", "Agent changed the fixed repository snapshot")
        if error is None and not execution.success:
            error = BenchError(
                execution.error_type or "AGENT_ERROR",
                execution.error_message or "Agent invocation failed",
            )
        if self.capture:
            self.capture(raw)
        if error:
            if audit_item is not None and execution.started:
                audit_item["status"] = error.kind
            store.checkpoint(result, stage + ":failed")
            raise error
        store.checkpoint(result, stage + ":completed")
        return execution.final_text
