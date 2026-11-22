# PLAN

Goal: full-scope semantic layer built in phases. Each phase ships something usable and has acceptance criteria.
Constraint: free to build and use (ADR-009).

**Status: Phases 0-6 built and tested (53 tests). `semlayer eval --oracle` is clean; LLM eval results are in EVAL.md.**

## Phase 0: Skeleton
- uv project, single package with subpackages (ADR-001), CI, lint, tests
- `core`: Pydantic models (Metric, Entity, Edge, Rule), `draft → certified → deprecated` state machine
- `examples/jaffle_shop` seed data (DuckDB)
- **Done when:** tests green in CI; models round-trip to YAML. ✔

## Phase 1: Draft and certify metrics
- `ingest` (schema, query log, dbt manifest docs), `lineage` (sqlglot), `draft` (statistics + optional LLM wording that includes the filters)
- `registry` (YAML + SQLite index, audit log, pending revisions), `cli` (incl. `rule-add`, `reindex`)
- **Done when:** demo log yields drafts with lineage and source queries; owner required to certify. ✔

## Phase 2: Serve and evaluate
- `compiler` (built-in; dimensions pinned by an equality filter are not grouped), `serve` (service + MCP server, certified only, usage logged)
- `evaluate` + 33 tuned and 18 held-out questions; arms: oracle, layer, baseline, baseline-hints
- **Done when:** eval reports answer accuracy and abstention precision. ✔

## Phase 3: Conflicts and review UI
- `conflicts`: variants and duplicates, structural diffs, ranked options (split / pick)
- Review UI (FastAPI + HTMX) ranked by demand
- **Done when:** seeded conflicts are detected and resolved. ✔

## Phase 4: Drift and feedback
- `monitor`: schema drift, query-pattern drift, value anomalies → pending revisions with diffs; debounced; severity split
- Feedback: demand, clustered gaps, flagged metrics, minimum-volume guard, role-only logging
- **Done when:** a schema change opens a revision for affected metrics only. ✔ (verified with a dropped column)

## Phase 5: Graph
- `graph`: entities/edges drafted from foreign-key naming; `describe_graph`, `traverse_graph`; filters limited to certified dimensions; metric → entity link; `entity_keys` on queries
- **Done when:** multi-hop questions answered through describe_graph → traverse_graph → governed metric. ✔ (verified with a real agent)

## Phase 6: Policy
- `policy`: default deny, certified rules only, deny beats allow, row filters, dimension restrictions, sensitive-column suggestions, replay
- **Done when:** replay and service tests show no unauthorized result. ✔

## Follow-ups (not built)
| Item | Notes |
|---|---|
| Suspend or flag a live definition when its source column disappears | today it stays live until a human reviews the drift revision (queries that use it fail closed) |
| Auto-fix drift revisions for dropped dimensions | a dropped measure or filter column still needs a human |
| Authentication for roles and the review UI | roles are asserted by the caller |
| Dimension values in `describe_metric`, filtered by role | agents currently discover values by grouping |
| MetricFlow / Cube adapter | implement the `Compiler` protocol (ADR-002) |
| Joins in metrics | needs multi-table definitions + join paths from the graph |
| Embedding-based clustering | structural clustering only today |
| Eval arm 3 (uncertified drafts) | to show certification matters |
| Other warehouses | connection adapter for Postgres etc. |
| Org-data owner inference | owners are suggested from query-log users only |

## Risks
| Risk | Mitigation |
|---|---|
| Rubber-stamp certification | show filters next to descriptions in review; track edit ratio (audit log) |
| LLM descriptions that omit conditions | the description prompt includes filters; reviewers must read the logic |
| Routing sensitive to catalog text | re-run the eval after any description, rule or metric change |
| Garbage in query logs | usage is shown as evidence, never auto-certifies |
| Scope creep | phase gates; ADR for each new decision |