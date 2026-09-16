# Independent implementation-spec audit

Read the original task, inspect the fixed repository, and evaluate current_spec.
Find material errors, omissions, unsupported assumptions, contradictions or
ambiguous decisions that would prevent an engineer implementing the original task
correctly. Check proposed behavior against the repository and consider realistic
compatibility, error paths and acceptance tests. Do not demand unrelated features,
cosmetic changes, speculative generalization, or changes to correct existing behavior.

Use blocking only for a concrete issue requiring a spec correction to implement the
task correctly. Use non_blocking for optional suggestions. Cite precise spec and/or
repository evidence; say what must change. This audit is independent: do not consult
previous audits, sessions, upstream fixes or gold tests. Do not repair the spec.

Return ONLY one JSON object, without fences or extra text. Its exact keys are
status and issues. status is "issues_found" if at least one issue is blocking;
otherwise it is "clean" (non_blocking issues may remain). Every issue has exactly
these nonempty string fields: id, severity, title, description, evidence,
required_change. severity is "blocking" or "non_blocking". IDs are unique within
this response. Do not invent issues to fill a quota. With no issues return:
{"status":"clean","issues":[]}
