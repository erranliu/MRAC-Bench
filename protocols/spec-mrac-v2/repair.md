# Repair every audit finding in the copied Spec

Use source intent and fixed_repository_head, reading only named symbols, explicitly
referenced documents and direct dependencies needed to resolve these findings.
Read relevant repository operational rules at that commit using ordinary file
tools in the isolated checkout. If source_spec names
another baseline, reconcile the discrepancy against the captured execution commit;
do not silently switch repositories, fetch another baseline or use a moving branch.

Within the user's objective and authorized scope, resolve design gaps autonomously
using the Spec's intent and verifiable evidence. Record the chosen behavior and
rationale, distinguishing existing constraints from decisions introduced in this
revision so the next independent auditor can assess them. An issue's category
alone is not a reason to stop and does not authorize expanding the user's scope.
Do not fabricate external facts or evidence. When a necessary fact or choice cannot
be established within authorized scope, state the concrete missing input and ask
for it directly; keep every finding pending in FIX.

Close every supplied finding, including P3, with a correction supported by evidence.
There is no acceptance, rejection, deferral, or exception classification step.
For new design decisions, record the chosen behavior and rationale in the Spec and
the final summary instead of claiming the decision uniquely follows from the
original source. Use supplied user_responses as explicitly provided later input,
not as proof that an earlier Spec already contained the decision. Do not implement
product code or add a separate design/Plan. Edit only spec_path in the isolated
checkout. Save the complete revised Spec there; omit audit IDs and workflow
history from its body. Preserve correct existing content. Briefly summarize the
changes and supporting evidence in the final response. The runner reads the
saved file and records its diff; it does not parse the summary.

If necessary information is unavailable, leave spec_path unchanged and return
only this short JSON response:
{"disposition":"needs_input","reason":"Specific fact or choice that cannot be established within scope","questions":["Self-contained question needed to finish the correction"]}
This preserves FIX and all pending findings. Do not save a partial Spec or mark
some findings resolved in needs_input.
