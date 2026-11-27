# PLAN

Goal: full-scope semantic layer built in phases. Each phase ships something usable and has acceptance criteria.
Constraint: free to build and use (ADR-009; for Phases 7-11 see ADR-024 and ADR-025).

**Status: Phases 0-11 built, tested (88 offline tests + live GCP verification), and documented. `semlayer eval --oracle` is 33/33 on both DuckDB and BigQuery MCP; Gemini & Qwen eval results are in EVAL.md.**

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

## Phases 7-11: Gemini API and BigQuery over MCP (completed)

### Target
```
agent --MCP (stdio)--> semlayer (local) --MCP (HTTPS)--> BigQuery MCP --> BigQuery sandbox
                         |  certified metrics, policy
                         +--> Gemini API (default LLM)  |  Ollama (option)
```

### Ground rules (ADR-025)
- No billing account is linked to any project, including the one that owns the Gemini API key.
- No Cloud Run, Artifact Registry, Cloud Build, Secret Manager or Firestore.
- Demo data only: the free Gemini tier uses prompts and responses to improve Google products.
- DuckDB stays the default warehouse; CI needs no key, no GCP account and no network calls to Google.
- Python dependencies are managed with uv.

### Phase 7: Check the assumptions
Manual setup: one GCP project in BigQuery sandbox mode, the BigQuery API, an AI Studio key.
- Gemini: one call through the OpenAI-compatible endpoint with `gemini-3.5-flash-lite`; record whether `json_object` and `reasoning_effort: low` are accepted, the token budget needed for a non-empty reply, and the free-tier limits AI Studio shows for the project
- BigQuery MCP: list tools; run one `dryRun` and one `execute_sql_readonly`; record the row and error format
- IAM: service account with `mcp.toolUser`, `bigquery.jobUser`, `bigquery.dataViewer`; confirm local impersonation works without billing
- **Done when:** every item above works with no personal billing account and the findings are written into ADR.md. ✔

### Phase 8: Gemini as the default LLM
- `core/llm.py`: provider `gemini` (reuses `OpenAICompatLLM`); key read from `GEMINI_API_KEY`; fixed Google endpoint
- Configurable output token budget and reasoning effort
- Pacing (requests per minute) and backoff on HTTP 429, honouring `Retry-After` when sent
- `evaluate`: an LLM transport or quota error stops the run (ADR-029)
- Leave values of sensitive columns out of eval prompts
- `semlayer.yaml`, README, SECURITY.md: Gemini default, Ollama documented as the option
- Tests with a fake HTTP server: provider selection, missing key, 429 retry, eval stop
- **Done when:** `make lint test` passes with no key; `demo` writes Gemini descriptions; switching to `llm_provider: ollama` needs config changes only; tuned and held-out results are in EVAL.md beside the Qwen results. ✔

### Phase 9: Warehouse interface (no behaviour change)
- `Warehouse` protocol: `query(sql, params)`, `schema()`, dialect
- DuckDB implementation; move direct connection use out of service, graph, ingest, drift, eval and CLI
- **Done when:** existing tests pass and `eval --oracle` is 33/33. ✔

### Phase 10: BigQuery over MCP
- Optional dependency group `gcp` (`google-auth`); the MCP client comes from the existing `mcp` package
- `BigQueryMCP` warehouse that calls only `execute_sql_readonly`
- Literal rendering through sqlglot (ADR-027); BigQuery dialect for table names, dates and the entity-key filter
- Safeguards (ADR-028): dry run with a byte cap; refuse unfinished jobs and truncated results; type conversion from the result schema
- Seed script that loads the demo tables with `bq load` (re-run after the 60-day sandbox expiry)
- Tests against a fake MCP server, including hostile filter values and entity keys
- **Done when:** `eval --oracle` is 33/33 on BigQuery; hostile-input tests pass offline and against the sandbox; `execute_sql_readonly` blocks writes. ✔

### Phase 11: End to end and docs
- MCP client -> semlayer (stdio) -> BigQuery MCP (`scripts/mcp_check.py --warehouse bigquery`)
- Run both LLM eval sets on BigQuery
- Update README, ARCHITECTURE.md, SECURITY.md, EVAL.md, GEMINI.md; extend `scripts/mcp_check.py`
- **Done when:** a fresh checkout can follow the README to a working setup, and CI passes without credentials. ✔

### Deferred (needs a billing account)
Cloud Run hosting, streamable HTTP transport, identity-to-role mapping, Firestore usage log.

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
| Free-tier models or limits change | model and limits are config; Phase 7 records them; Ollama remains |
| Quota errors distort eval scores | stop the run on transport or quota errors (ADR-029) |
| Data sent to Google | demo data only; documented in SECURITY.md; Ollama for anything else |
| Injection through rendered literals | typed allow-list, sqlglot rendering, hostile-input tests, read-only role |
| Sandbox tables expire | re-run the seed script |
