# SECURITY

## Controls in code
| Threat | Control |
|---|---|
| Agent bypasses layer | no SQL tool; agents connect only to `semlayer` MCP (never given BigQuery credentials); DuckDB opened read-only; `BigQueryMCP` calls only `execute_sql_readonly` |
| Unlisted dimensions/filters | compiler allows only the certified definition's dimensions |
| SQL injection via values | DuckDB uses bound parameters; `BigQueryMCP` (`ADR-027`) parses SQL via `sqlglot`, allows only typed scalar/flat-list AST literals (rejecting null bytes, non-finite floats, nested objects, and multi-statement/non-`SELECT` queries), and re-parses rendered BigQuery SQL |
| Runaway BigQuery scans / partial results | `BigQueryMCP` (`ADR-028`) runs a pre-flight dry-run capped at `bq_max_bytes` and refuses incomplete jobs or paginated/truncated results |
| Sensitive data sent to cloud LLM | eval prompts strip distinct-value hints for columns matching `policy.SENSITIVE`; use demo/non-sensitive data on free AI Studio Gemini tiers or switch `llm_provider` to `ollama` / `openai` (local) |
| Malicious row filter | `row_filter` must parse as a plain predicate (no subqueries, no multiple statements) |
| Wrong policy | default deny; only certified rules; deny beats allow; replay before certifying |
| Row-level leakage via graph | traversal requires an allow-all role (ADR-014) |
| DB errors leaking data | errors fail closed and are logged as `error` events |
| Log privacy | usage logs store roles, never user identities; feedback ignores signals below a minimum volume |
| Stale owners | owner required to certify; revisions keep owner |
| Secrets | `.env` and local state are git-ignored; use ADC / service-account impersonation (`bq_service_account`) instead of JSON key files; gitleaks runs in CI |
| Probing columns through graph filters | graph filters limited to columns that certified metrics expose as dimensions, minus the role's denied dimensions |

## Known gaps
- Roles are asserted by the caller (`as_role`); wire your own authentication in front of the MCP/HTTP server.
- The review UI has no login; run it locally or behind your own auth.
- Metric `filters` are trusted SQL authored by certified definitions (reviewers must read them).
- Query-log text is untrusted: it is parsed, never executed, and shown to reviewers as evidence.
- A certified definition whose source column disappears stays live until a human reviews the drift revision; queries that use it fail closed with an error and show up as flagged metrics in `feedback`.
