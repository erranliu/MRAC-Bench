# Initial copied-Spec audit

Audit the copied Spec for concrete gaps that prevent its stated benefit from
closing within its declared scope: missing or contradictory required behavior,
ownership or truth source, success or failure outcome, dependency boundary, or
observable acceptance. Inspect repository code and related Specs as needed to
establish existing contracts and boundaries, but report closure gaps in the copied
Spec rather than code defects. Treat a gap as P1 when the stated benefit cannot be
delivered without resolving it. Report closure problems and evidence only; do not
propose repairs or report concerns that do not affect closure.

The supplied source_spec is immutable intent. The copied current_spec is the audit
target. Inspect code and in-repository related Specs only at fixed_repository_head.
Use the supplied mrac_repository MCP tools for source search and file reads; do not
use shell commands to inspect the repository.
External related Specs are limited to the supplied related_specs snapshots.
Evidence must cite current_spec; cite code/in-repository Specs against the fixed
base, and cite each external related Spec by its supplied file and SHA-256.
Do not consult prior audits, repairs, or sessions. Do not invent findings.

Return JSON only, with exactly this shape and the supplied audit_id:
{"audit_id":"...","findings":[{"severity":"P1","title":"...","evidence":"..."}]}
severity must be P0, P1, P2, or P3. All fields are nonempty strings. Use an empty
findings array when there are no findings. Do not assign finding IDs or return fixes.
