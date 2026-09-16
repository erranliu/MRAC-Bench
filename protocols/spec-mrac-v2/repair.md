# Repair all accepted findings in the copied Spec

Use source intent and fixed_repository_head, reading only named symbols, explicitly
referenced documents and direct dependencies needed to resolve these findings.
Read relevant repository operational rules at that commit. If source_spec names
another baseline, reconcile the discrepancy against the captured execution commit;
do not silently switch repositories, fetch another baseline or use a moving branch.

Within the user's objective and authorized scope, resolve design gaps autonomously
using the Spec's intent and verifiable evidence. Record the chosen behavior and
rationale, distinguishing existing constraints from decisions introduced in this
revision so the next independent auditor can assess them. An exception category
alone is not a reason to stop and does not authorize expanding the user's scope.
Do not fabricate external facts or evidence. When a necessary fact or choice cannot
be established within authorized scope, state the concrete missing input and ask
for it directly; keep every accepted finding pending in FIX.

Close every accepted finding, including accepted P3, with a correction and evidence.
For new design decisions, record the chosen behavior and rationale in the Spec and
the correction record instead of claiming the decision uniquely follows from the
original source. Use supplied user_responses as explicitly provided later input,
not as proof that an earlier Spec already contained the decision. Do not implement
product code or add a separate design/Plan. Return the complete revised Spec; omit
audit IDs and workflow history from its body. Preserve correct existing content.

Return ONLY one of these shapes, using the supplied audit_id:
{"audit_id":"...","disposition":"continue","spec":"Complete revised Spec text","fixes":[{"finding_id":"F1","summary":"Correction, chosen behavior and rationale","evidence":"Source intent, fixed repository evidence, or explicitly supplied user decision supporting the correction"}]}
Include exactly one closure for every accepted finding. The Spec must be nonempty
and its bytes must change. Do not claim resolution through an unchanged document.

If necessary information is unavailable, return:
{"audit_id":"...","disposition":"needs_input","reason":"Specific fact/choice that cannot be established within scope","questions":["Self-contained question needed to finish the correction"]}
This preserves FIX and all pending findings; it is not BLOCKED, clean, or completion.
Do not include a partial Spec or mark some findings resolved in needs_input.
