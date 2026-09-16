# Repair accepted initial Spec findings

Close every accepted spec-init finding in one coherent copied-Spec repair. Use
immutable task/source intent and the controlled baseline only to clarify existing
intent. Make the stated benefit, ownership/truth, success and failure outcomes,
dependency boundary, and acceptance mutually consistent. Do not invent behavior
or durable mechanisms merely to satisfy findings. Block if any accepted gap cannot
be closed without a product decision, scope expansion, external dependency, or
unspecified later work; otherwise continue directly to spec-freeze-loop.

Read only Spec-named symbols and direct semantic dependencies. Verify the fixed
baseline SHA and show the existing rule that entails each correction. Source
intent wins over code. If the source's explicit implementation baseline is not
the supplied fixed_repository_head and is needed for proof, return block; do not
fetch or use a live branch tip. Do not leave any accepted finding unresolved.
Do not implement product code or create another design. Preserve correct detail.
The complete replacement Spec must not discuss audits, finding IDs, or repair history.

Return ONLY one JSON object, using the supplied audit_id.
To continue:
{"audit_id":"...","disposition":"continue","spec":"# Complete replacement Spec\n...","fixes":[{"finding_id":"F1","summary":"Correction made","evidence":"Source intent and fixed-baseline evidence entailing this correction"}]}
Include exactly one fix for every accepted finding. The spec must have a level-one
Markdown title and substantive body and must change the current Spec bytes.
If any accepted finding cannot be resolved within authority, return instead:
{"audit_id":"...","disposition":"block","reason":"Specific unresolved decision or dependency"}
Do not include a partial replacement Spec when blocked.
