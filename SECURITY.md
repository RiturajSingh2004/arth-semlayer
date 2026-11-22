# SECURITY

## Controls in code
| Threat | Control |
|---|---|
| Agent bypasses layer | no SQL tool; agents use MCP only; DuckDB opened read-only |
| Unlisted dimensions/filters | compiler allows only the certified definition's dimensions |
| SQL injection via values | filter and entity-key values are bound parameters; identifiers validated by regex |
| Malicious row filter | `row_filter` must parse as a plain predicate (no subqueries, no multiple statements) |
| Wrong policy | default deny; only certified rules; deny beats allow; replay before certifying |
| Row-level leakage via graph | traversal requires an allow-all role (ADR-014) |
| DB errors leaking data | errors fail closed and are logged as `error` events |
| Log privacy | usage logs store roles, never user identities; feedback ignores signals below a minimum volume |
| Stale owners | owner required to certify; revisions keep owner |
| Secrets | `.gitignore` for local state; gitleaks in CI |
| Probing columns through graph filters | graph filters limited to columns that certified metrics expose as dimensions, minus the role's denied dimensions |

## Known gaps
- Roles are asserted by the caller (`as_role`); wire your own authentication in front of the MCP/HTTP server.
- The review UI has no login; run it locally or behind your own auth.
- Metric `filters` are trusted SQL authored by certified definitions (reviewers must read them).
- Query-log text is untrusted: it is parsed, never executed, and shown to reviewers as evidence.
- A certified definition whose source column disappears stays live until a human reviews the drift revision; queries that use it fail closed with an error and show up as flagged metrics in `feedback`.