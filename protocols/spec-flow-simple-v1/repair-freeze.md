# Repair accepted freeze-audit findings

Repair every accepted finding in the copied Spec. Use immutable intent and the
fixed baseline only. Read Spec-named symbols and direct semantic dependencies
in the fixed checkout using ordinary file tools.
Resolve ambiguities with the narrowest coherent correction supported by the
supplied intent and baseline. State any necessary assumptions explicitly; do not
present them as verified facts. Save a complete revised Spec for re-audit.

Verify fixed_repository_head and use source/baseline evidence for each correction.
Source intent wins over code. Use only the supplied fixed_repository_head; do not
fetch or use a live branch tip. Distinguish baseline facts from intended changes.
Do not introduce additional changes merely to satisfy an auditor.
Close all accepted findings, including accepted P3.
Do not implement code or create another design. Preserve correct detail. The
replacement Spec must not discuss audits, finding IDs, or repair history.

The isolated checkout contains the copied Spec at spec_path. The original
source intent and accepted findings are supplied in the task input. Preserve
meaningful source metadata; it may precede the title. Change the Spec bytes
and keep every correct detail. Edit no other project file. A brief final
summary is enough; the saved Spec file is the replacement artifact.
