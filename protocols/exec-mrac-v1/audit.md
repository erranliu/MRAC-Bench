# Independent implementation audit against Spec and complete diff

Compare the immutable execution_spec with the complete candidate diff and minimal
supporting source. Report concrete correctness, regression, recovery, ownership,
required-deletion or test defects; ignore optional hardening and unrelated style
or redesign preferences. The review target is whether this implementation satisfies
the Spec, not whether the Spec should be rewritten. Read relevant repository rules.

Inspect diff_path in full, including additions, deletions and binary changes. Check
relevant callers/consumers and direct dependencies in the candidate checkout as
needed; use base_head for before/after comparisons. Unlike a published PR workflow,
this candidate includes uncommitted and untracked product files: do not ignore them.
The runner's workflow evidence lives outside the checkout and is not product code.
Use read-only Git commands to inspect tracked changes and enumerate untracked
additions; ordinary git diff alone does not cover the complete candidate.
Use git ls-files --others --ignored --exclude-standard -- <relevant-path> to query
ignored paths when needed. Report required product code or resources hidden outside
the candidate patch. Generated test/build outputs alone
are not product defects. Assess supplied validation records critically; absent,
failed or claimed verification is not proof of correctness.

Be read-only. Do not edit, implement, run tests/builds/Unity, propose a new Plan or
speculate about unrelated scenarios. Do not read past audits, repair explanations,
other runs, or previous model sessions. Report findings and concrete evidence, not
a prescribed repair design. Each finding must cite the relevant Spec obligation
and changed path/symbol/diff or necessary supporting source. Do not invent findings
to fill a quota. Every reported finding, including P3, must be addressed; there is
no non-blocking exemption or deferred finding that can coexist with clean.

Return ONLY:
{"audit_id":"<supplied audit_id>","spec_sha256":"<supplied spec_sha256>","candidate_sha256":"<supplied candidate_sha256>","findings":[{"severity":"P1","title":"Concrete defect","evidence":"Spec section and candidate path/symbol showing the failure"}]}
Severity is P0, P1, P2 or P3. Use an empty findings array only when there are no
issues. Copy the supplied identities exactly. A boundary/access failure must be
reported as an execution failure, never as a fabricated clean result.
