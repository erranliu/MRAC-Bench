# Standalone Spec freeze audit

Inspect only the standalone specification. Report concrete defects in standalone
value, internal consistency, ownership or authoritative-state clarity, lifecycle
or failure boundaries, completeness, or observable acceptance. Inspect no code or
Plan, infer no unlisted scenario, and propose no repair.

Use only current_spec. Evidence must cite this Spec. Do not read source intent,
repository files, related designs, prior audits, repairs, or sessions. Do not use
tools or invent findings to fill a quota. This audit does not certify original-intent
entailment, implementation feasibility, or product test results.

Return JSON only, with exactly this shape and the supplied audit_id:
{"audit_id":"...","findings":[{"severity":"P1","title":"...","evidence":"..."}]}
severity must be P0, P1, P2, or P3. All fields are nonempty strings. Use an empty
findings array when there are no findings. Do not assign finding IDs or return fixes.
