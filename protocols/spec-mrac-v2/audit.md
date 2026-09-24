# Independent repository-backed Spec audit

对 Spec 的正确性和可实施性做审计。

The target is current_spec; spec_sha256 identifies its saved bytes. The immutable
source_spec and source_spec_sha256 provide the original intent. Use the supplied
repository_path and fixed_repository_head. Every audit must inspect relevant
repository content at that commit, including when it returns no findings.
Read only; do not edit files, build, or run Unity. Read repository evidence using
the supplied mrac_repository MCP tools. Verify repository_head, locate relevant
files and read their content at the fixed commit. If the Spec hash differs or the baseline
cannot be inspected, report the boundary/access failure instead of a clean audit.
Do not read prior findings, repair notes, other runs, or conversation history.

Return JSON only with audit_id and findings:
{"audit_id":"...","findings":[{"severity":"P1","title":"...","evidence":"..."}]}
Severity must be P0, P1, P2, or P3. Evidence must cite this Spec and relevant
repository paths and symbols/sections at the fixed commit. If a required symbol
is absent, cite the inspected owner/caller or contract file and explain its absence;
do not invent a path. Use an empty findings array when clean.
