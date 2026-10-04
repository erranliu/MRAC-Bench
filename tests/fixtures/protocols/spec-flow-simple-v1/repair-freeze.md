# Repair accepted freeze-audit findings

Repair every accepted finding in the copied Spec. Use immutable intent and the
fixed baseline only. Read Spec-named symbols and direct semantic dependencies
with the supplied mrac_repository MCP tools; do not use shell commands to inspect
the repository.
Resolve ambiguities with the narrowest coherent correction supported by the
supplied intent and baseline. State any necessary assumptions explicitly; do not
present them as verified facts. Return a complete replacement Spec for re-audit.

Verify fixed_repository_head and show the source/baseline evidence supporting each correction.
Source intent wins over code. Use only the supplied fixed_repository_head; do not
fetch or use a live branch tip. Distinguish baseline facts from intended changes.
Do not introduce additional changes merely to satisfy an auditor.
Close all accepted findings, including accepted P3.
Do not implement code or create another design. Preserve correct detail. The
replacement Spec must not discuss audits, finding IDs, or repair history.

The isolated workspace contains spec.md (a copy of the current Spec),
source-spec.md (immutable source intent), and accepted-findings.json.
Use mrac_candidate.candidate_read/search to inspect them and
mrac_candidate.candidate_edit to change spec.md. Do not use shell or
apply_patch tools. Preserve meaningful source metadata;
it may precede the title. Change the Spec bytes and keep every correct detail.
Do not edit or create any other workspace file. A brief final message is enough;
only the saved spec.md is used as the replacement Spec.
