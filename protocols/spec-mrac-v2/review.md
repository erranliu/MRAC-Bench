# Supervise the audit and assess every finding

Check whether repository_review actually supports the audit conclusions and covers
the relevant implementation assumptions. The controller verifies commit and file
existence, not the truth, coverage or relevance of the evidence. Read the minimum
necessary fixed-baseline sources and operational instructions to check this, even
for an empty findings array. If the evidence is irrelevant or insufficient, mark
repository_assessment insufficient and state the missing verification; the audit
remains unfinished and cannot count as clean.

Assess each supplied finding against current_spec, immutable source_spec and the
fixed repository baseline. Do not introduce extra findings or rewrite the auditor's
findings. Submit exactly one decision per assigned finding_id. Reject only with
concrete counter-evidence; defer only P3 and explain why. Every accepted finding
requires a correction, including accepted P3.

Optional exception metadata (product-decision, scope-expansion, external-dependency)
records the type of issue only. It does not change the workflow, require approval,
or grant authority to expand scope. All accepted findings enter FIX. Within the
user's objective and authorized scope, the repairer may resolve design gaps using
intent and verifiable evidence, distinguishing introduced decisions from existing
constraints. A finding's category alone is not a reason to stop. Do not fabricate
external facts or dismiss a real defect merely because a decision is needed.

Return ONLY:
{"audit_id":"...","repository_assessment":{"status":"supported","reason":"Why the inspected evidence supports the audit and covers relevant assumptions"},"decisions":[...]}
repository_assessment.status is supported or insufficient; reason must be nonempty.
This explicit assessment records the source skill parent's evidence-checking duty.
Each decision is exactly one of:
{"finding_id":"F1","outcome":"accepted"}
{"finding_id":"F1","outcome":"accepted","exception":"product-decision"}
{"finding_id":"F1","outcome":"rejected","reason":"Concrete counter-evidence"}
{"finding_id":"F1","outcome":"deferred","reason":"Why this P3 may be deferred"}
Both rejected and deferred require reason. Accepted decisions carry no reason.
Do not choose a next action or rewrite the Spec. Empty findings require empty decisions.
