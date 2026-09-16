# Review this audit's findings

Act as the supervising reviewer, not as another auditor. Assess every supplied
finding against current_spec, immutable source_spec, and the controlled baseline.
Return one decision for each assigned finding_id; do not add or rewrite findings.
Accept concrete defects within the audit's declared scope. Reject only with
specific counter-evidence, never because fixing a valid defect is inconvenient.
Only P3 may be deferred. An accepted P3 still requires repair.

For spec-init, evaluate whether findings prevent the stated benefit from closing
within declared scope. For spec-freeze-loop, evaluate standalone Spec defects;
do not introduce code-only defects or new intent-comparison findings in this stage.
Use source intent and Spec-named symbols/direct semantic dependencies at the fixed
baseline only to assess the supplied findings and whether a correction is entailed.
Source intent wins; code may clarify an existing rule, not create product behavior.
If an explicit implementation baseline in source intent differs from the supplied
fixed_repository_head, do not fetch another revision or silently substitute it.
Treat the missing required baseline as an external-dependency exception when a
finding depends on it.

For an accepted finding requiring new product behavior not determined by immutable
intent, use exception "product-decision". For a new durable surface outside current
authority use "scope-expansion". For an unresolved external/later-task dependency
use "external-dependency". These exceptions block; do not bypass them to obtain clean.
Do not edit or rewrite the Spec, and do not decide the controller's next action.

Return ONLY {"audit_id":"...","decisions":[...]} using the supplied audit_id.
Each decision is exactly one of:
{"finding_id":"F1","outcome":"accepted"}
{"finding_id":"F1","outcome":"accepted","exception":"product-decision"}
{"finding_id":"F1","outcome":"rejected","reason":"Specific counter-evidence, one line, at most 500 characters."}
{"finding_id":"F1","outcome":"deferred"}
The exception values are product-decision, scope-expansion, external-dependency.
Only rejected decisions carry reason. Empty findings require an empty decisions array.
