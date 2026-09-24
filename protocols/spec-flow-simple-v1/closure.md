# Verify accepted repair closure

Use ordinary file read tools to inspect this isolated comparison workspace:
spec.md is the proposed complete
replacement, previous-spec.md is its immediate predecessor, source-spec.md is
the immutable source intent, accepted-findings.json lists the accepted issues,
and diff.txt records the change. Determine whether the proposed Spec actually
closes each accepted issue without replacing the document with a placeholder.
Inspect spec.md, previous-spec.md, source-spec.md, accepted-findings.json,
and diff.txt. Read additional sections when needed. Do not answer from file
names alone.

Reply `CLOSED` when every accepted issue is closed. Otherwise give one line per
unresolved accepted issue as `F1: concrete reason`. Do not infer that a change
is sufficient merely because a section or a keyword was added. Do not edit files.
