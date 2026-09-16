# Independent repository-backed Spec audit

Audit the Spec and verify its implementability against the fixed repository commit.
Inspect relevant code, configuration, tests, contracts and related Specs, including
named symbols, callers/consumers and direct dependencies. Use git show
<fixed_repository_head>:<path> or equivalent fixed-commit reads, never a moving
branch tip or uncommitted product code. Check value, consistency, ownership/truth,
lifecycle/failure boundaries, completeness, observable acceptance and feasibility
within scope. Distinguish existing capabilities from explicitly proposed changes:
absence of a proposed capability is not itself a defect. Report concrete missing
prerequisites, incompatible contracts, unowned work, or acceptance that cannot be
achieved within scope. Identify unavailable resources/dependencies rather than
assuming them. Use the immutable source_spec when needed to check intent or new
design choices. Read relevant fixed-commit repository instructions as operational
rules. The review target is current_spec, not unrelated code defects or a new Plan.
Do not edit, implement, build, run Unity, or propose repairs. Cite the Spec and
fixed-commit repository evidence for findings.

Every result, including clean, requires repository_review with concrete inspected
paths, symbols/sections and conclusions covering relevant implementation assumptions.
Inspect the actual repository; a token unrelated file is not sufficient evidence.
If a required symbol is absent, cite the inspected existing owner/caller/contract
file and explain the absence. Do not invent a path. Do not read earlier audits,
repair notes, other runs, or conversation history.

The supplied spec_sha256 identifies the saved current Spec bytes; the runner
checks that boundary. If the repository baseline cannot be inspected, report the
concrete access/boundary failure rather than fabricate evidence or a clean result.

Return ONLY JSON with exactly these keys, using the supplied audit_id and fixed commit:
{"audit_id":"...","repository_review":{"base_head":"<fixed_repository_head>","checks":[{"path":"repo/relative/tracked-file","symbol":"name or document section","conclusion":"Concrete repository fact and its impact on Spec feasibility"}]},"findings":[{"severity":"P1","title":"...","evidence":"Spec section plus repository path, symbol/section and fixed commit"}]}
checks must be nonempty even when findings is empty. All paths must identify tracked
files at the fixed commit. severity is P0, P1, P2, or P3. Use an empty findings array
when clean. Do not add findings to meet a quota. Report no fixes or finding IDs.
