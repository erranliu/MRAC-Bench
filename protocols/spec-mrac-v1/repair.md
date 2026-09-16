# Repair an implementation spec

Inspect the original task, fixed repository, current_spec and current_audit.
Address every blocking issue in the current audit while preserving correct
behavior and useful detail. Reconcile changes against the task and source code;
do not add unrelated scope. If an audit recommendation conflicts with evidence,
make the spec's intended behavior and evidence explicit so it is implementable.
Do not refer to the audit, issue IDs, earlier revisions, or the repair process in
the finished document. Do not implement source code.

Return ONLY the complete replacement Markdown spec beginning with a level-one
`# ` title, including unchanged sections. No patch/diff, conversational preamble,
postscript, or enclosing code fence. The repository is read-only; the runner saves
your final reply as a new artifact snapshot.
