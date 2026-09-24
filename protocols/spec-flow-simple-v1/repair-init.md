# Repair accepted initial Spec findings

Close every accepted spec-init finding in one coherent copied-Spec repair. Use
immutable task/source intent and the controlled baseline only to clarify existing
intent. Make the stated benefit, ownership/truth, success and failure outcomes,
dependency boundary, and acceptance mutually consistent. Resolve ambiguities with
the narrowest coherent correction supported by the supplied intent and baseline.
State any necessary assumptions explicitly; do not present them as verified facts.
Save a complete revised Spec for the next spec-freeze-loop audit.

Read only Spec-named symbols and direct semantic dependencies in the fixed
checkout. Use ordinary file tools to inspect the project and edit the named
Spec file. Verify the fixed baseline SHA and use source/baseline evidence. Source
intent wins over code. Use only the supplied fixed_repository_head; do not fetch
or use a live branch tip. Distinguish baseline facts from intended changes.
Do not leave any accepted finding unresolved.
Do not implement product code or create another design. Preserve correct detail.
The complete replacement Spec must not discuss audits, finding IDs, or repair history.

The isolated checkout contains the copied Spec at spec_path. The original
source intent and accepted findings are supplied in the task input. Preserve
meaningful source metadata; it may precede the title. Change the Spec bytes
and keep every correct detail. Edit no other project file. A brief final
summary is enough; the saved Spec file is the replacement artifact.
