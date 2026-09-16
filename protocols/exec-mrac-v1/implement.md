# Implement the fixed execution Spec

Read execution_spec and the dedicated checkout at base_head. Implement the complete
authorized Spec without inventing extra requirements. Read relevant repository
operational instructions, named symbols, callers and direct dependencies. Preserve
correct existing behavior and respect ownership, migration/deletion, failure and
acceptance contracts. The selected Spec is immutable: do not rewrite it or create
a different Spec/Plan to make the implementation pass.

Modify product files only in this checkout. New, modified and deleted files are all
part of the candidate. Do not hide required implementation in ignored files; keep
generated build/test outputs separate from product changes. Do not stage or commit;
the runner captures the entire product diff, including untracked and binary files.
Perform relevant verification where supported. Record exact commands and observed
results, including failed or unexecuted verification and its reason. Tests passing
does not substitute for the independent audits that follow. Never fabricate results.

If a necessary fact, dependency or baseline discrepancy cannot be resolved within
the Spec and authorized scope, describe the exact missing input and ask for it.
Preserve partial code and do not claim completion. Do not change the fixed baseline,
expand scope, install globally or publish anything to work around missing input.

Return ONLY one of:
{"disposition":"complete","summary":"Implemented behavior or evidence that the baseline already satisfies the Spec","validation":[{"command":"actual command or planned check","status":"passed","evidence":"Observed result or saved verification evidence"}]}
{"disposition":"needs_input","summary":"Concrete condition preventing completion","questions":["Specific missing fact or choice"],"validation":[{"command":"check","status":"not_run","evidence":"Why verification could not run"}]}
validation must be nonempty; its status is passed, failed, or not_run. Do not claim
tests ran when they did not. Do not return code patches as the final response: apply
the changes in the authorized checkout and report their outcome.
