# Repair accepted initial Spec findings

Close every accepted spec-init finding in one coherent copied-Spec repair. Use
immutable task/source intent and the controlled baseline only to clarify existing
intent. Make the stated benefit, ownership/truth, success and failure outcomes,
dependency boundary, and acceptance mutually consistent. Resolve ambiguities with
the narrowest coherent correction supported by the supplied intent and baseline.
State any necessary assumptions explicitly; do not present them as verified facts.
Return a complete replacement Spec for the next spec-freeze-loop audit.

Read only Spec-named symbols and direct semantic dependencies with the supplied
mrac_repository MCP tools; do not use shell commands to inspect the repository.
Verify the fixed
baseline SHA and show the source/baseline evidence supporting each correction. Source
intent wins over code. Use only the supplied fixed_repository_head; do not fetch
or use a live branch tip. Distinguish baseline facts from intended changes.
Do not leave any accepted finding unresolved.
Do not implement product code or create another design. Preserve correct detail.
The complete replacement Spec must not discuss audits, finding IDs, or repair history.

Return the complete replacement Spec in `spec` and one short `fixes` evidence
entry for each accepted finding ID. The output schema is supplied separately.
Preserve meaningful source metadata in the Spec; it may precede the title.
The replacement must change the current Spec bytes.
