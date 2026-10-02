# Fixed-repository read preflight

Before any Spec audit, verify that this Codex invocation can inspect the fixed
repository through the mrac_repository MCP tools. Do not infer access from the
supplied path or repository manifest.

Perform these steps using MCP tools:

1. Call repository_head; its observed HEAD must equal fixed_repository_head.
2. Call repository_search with exactly {"max_results":1}. Omit query and globs;
   the tool supplies source-code globs. This is a source-line sample, not a
   language guess or a broad keyword search.
3. Take the returned match's path and line. Call repository_read with that path,
   start_line equal to that line, and max_lines equal to 1.

Stop on HEAD mismatch, a transport/access failure, or no source match.

Do not write to the repository, inspect a live branch tip, or read outside the
fixed repository. This is an access check, not a Spec audit. The final message
is ignored: the controller verifies the successful MCP call records, observed
HEAD, search result, and matching source line before starting spec-init.
