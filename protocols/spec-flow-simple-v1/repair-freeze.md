# Repair accepted freeze-audit findings

Repair every accepted finding in the copied Spec. Use immutable intent and the
fixed baseline only. Read Spec-named symbols and direct semantic dependencies.
Continue only when one existing rule is provable; block on ambiguity, unresolved
findings, later-task dependence, new product behavior, or a new durable surface.

Verify fixed_repository_head and show the existing rule that entails each correction.
Source intent wins over code. If a required explicit implementation baseline in
source intent differs from the supplied fixed_repository_head, return block;
do not fetch or use a live branch tip. Do not introduce additional changes merely
to satisfy an auditor. Close all accepted findings, including accepted P3.
Do not implement code or create another design. Preserve correct detail. The
replacement Spec must not discuss audits, finding IDs, or repair history.

Return ONLY one JSON object, using the supplied audit_id.
To continue:
{"audit_id":"...","disposition":"continue","spec":"# Complete replacement Spec\n...","fixes":[{"finding_id":"F1","summary":"Correction made","evidence":"Source intent and fixed-baseline evidence entailing this correction"}]}
Include exactly one fix for every accepted finding. The spec must have a level-one
Markdown title and substantive body and must change the current Spec bytes.
If any accepted finding cannot be resolved within authority, return instead:
{"audit_id":"...","disposition":"block","reason":"Specific unresolved decision or dependency"}
Do not include a partial replacement Spec when blocked.
