# Repair the current implementation findings

Read the immutable execution_spec, current full candidate diff, current_findings
and minimal supporting source. Correct every supplied finding in product code,
including P3, while preserving the Spec's intent, correct behavior and scope.
Follow relevant repository operational rules. Do not rewrite the Spec, weaken its
acceptance criteria, add a new Plan, or mask a defect by deleting valid checks.

The fixed base_head remains the comparison baseline. Do not stage, commit, publish,
reset or change Git metadata. Make the corrections in this dedicated checkout;
the runner snapshots the full result, including untracked additions and binaries.
Keep required product files in the candidate patch instead of ignored output paths.
Run relevant verification and record observed outcomes honestly. Do not claim a
finding is fixed without a code change and evidence of its correction. The next
fresh audit independently checks the complete implementation.

If a finding conflicts with the fixed Spec, or necessary facts/dependencies cannot
be established within scope, explain the concrete missing input. Do not silently
discard findings, invent external facts or change the Spec to obtain clean. Supplied
user_responses may clarify execution conditions; they do not silently replace the
pinned Spec. A materially different Spec requires a separate run.

Return ONLY one of:
{"disposition":"complete","summary":"Corrections applied","validation":[{"command":"actual check","status":"passed","evidence":"Observed result"}],"fixes":[{"finding_id":"F1","summary":"Correction to product code","evidence":"Changed path/symbol and verification supporting closure"}]}
{"disposition":"needs_input","summary":"Concrete condition preventing repair","questions":["Required input"],"validation":[{"command":"check","status":"not_run","evidence":"Reason"}]}
Complete requires exactly one fix per current finding and an actual product diff
change. validation is nonempty; status is passed, failed, or not_run. needs_input
must not claim partial closure or include fixes. Apply code in the checkout rather
than returning a patch as the final response.
