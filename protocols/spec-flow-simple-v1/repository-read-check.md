# Fixed-repository read preflight

Before any Spec audit, verify that this Codex invocation can inspect the fixed
repository through the mrac_repository MCP tools. Do not infer access from the
supplied path or repository manifest.

Perform these steps using MCP tools and stop if one fails:

1. Call repository_head; its observed HEAD must equal fixed_repository_head.
2. Call repository_search without a query and with source-code globs. Select one
   returned repository-relative path and non-empty match.
3. Call repository_read on that path and read the line reported by the match.

Do not write to the repository, inspect a live branch tip, or read outside the
fixed repository. This is an access check, not a Spec audit. The final message
is ignored: the controller verifies the successful MCP call records, observed
HEAD, search result, and matching source line before starting spec-init.
