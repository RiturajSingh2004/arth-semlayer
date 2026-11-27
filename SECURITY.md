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
| Role spoofing via prompt injection | `semlayer serve --role <role>` (`bound_role` / `SEMLAYER_BOUND_ROLE`) binds caller identity at server start and rejects any caller-supplied `as_role` (`ADR-031`) |
| Indirect RBAC bypass via ratios/joins | `ratio`/`derived` metrics enforce RBAC + `row_filter` + `deny_dims` on every input metric (`ADR-033`); `<Entity>.<dim>` joins require safe `many_to_one` cardinality and unrestricted access on the joined entity (`ADR-034`) |
| Resource exhaustion / broken columns | configurable `max_rows`, `max_groups`, and `max_filter_list` caps; `semlayer canary` probes certified metrics with `LIMIT 0` and suspends broken definitions (`State.suspended`, never served) (`ADR-031`) |
| Unauthenticated Governance UI / key sprawl | `deploy/cloudrun/` deploys `--web` on Cloud Run with `--no-allow-unauthenticated` (Cloud Run IAM / IAP), injects `GEMINI_API_KEY` from GCP Secret Manager, and emits structured audit/refusal JSON to Cloud Logging (`ADR-037`) |
| Unmarked PII columns in BigQuery | `ingest` inspects Dataplex / Data Catalog policy tags (`INFORMATION_SCHEMA.COLUMN_FIELD_PATHS`) and merges tagged columns into drafted `deny_dims` rules (`ADR-036`) |
| Probing columns through graph filters | graph filters limited to columns that certified metrics expose as dimensions, minus the role's denied dimensions |

## Known gaps
- When `bound_role` is unset (multi-role local/eval mode), roles are asserted by the caller (`as_role`); start MCP servers with `semlayer serve --role <role>` in untrusted agent environments.
- Locally (`127.0.0.1`), the review UI has no built-in login; deploy behind Cloud Run IAM / IAP (`deploy/cloudrun/`) or a reverse-proxy auth layer when shared.
- Metric `filters` are trusted SQL authored by certified definitions (reviewers must read them).
- Query-log text is untrusted: it is parsed, never executed, and shown to reviewers as evidence.
