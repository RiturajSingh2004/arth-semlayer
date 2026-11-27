# ARCHITECTURE

## Flow
```
query log + schema (+dbt docs) → ingest → draft → conflicts → review queue → registry (certified)
                                                     ↑                          ↓
                                   monitor (drift) ──┘                   serve (MCP / API) → agents
                                   feedback ←──────────── usage log ←───────────┘
```

## GCP & MCP topology
```
agent ──MCP (stdio)──► semlayer (local: certified metrics, graph, policy)
                         ├──► DuckDB (local default)  OR  ──MCP (HTTPS)──► BigQuery MCP (`execute_sql_readonly`)
                         └──► Gemini API via AI Studio key (default LLM)  |  Ollama / OpenAI-compat (secondary local)
```

## Modules (`src/semlayer/`)
| Module | Responsibility |
|---|---|
| core | models, state machine, config + `.env` loader, LLM interface (`gemini`, `ollama`, `openai`, `none`) with pacing & 429 `Retry-After` backoff |
| warehouse | `Warehouse` protocol, `DuckDBWarehouse` (bound params), `BigQueryMCP` (Streamable HTTP client calling only `execute_sql_readonly`, `sqlglot` AST literal/dialect rendering, dry-run byte cap) |
| ingest | schema snapshot via `Warehouse.schema()`, query log (JSONL/SQL), dbt manifest docs |
| lineage | sqlglot column-level lineage |
| draft | single-table aggregate queries → metric drafts; evidence; owner suggestion; optional LLM wording (includes filters) |
| conflicts | variants (same concept, different logic) and duplicates (same logic, different names); split / pick |
| registry | YAML files (source of truth), SQLite index (rebuildable via `reindex`), audit log, pending revisions, optional git commit |
| compiler | metric → SQL (`Compiler` protocol, `SimpleCompiler` supporting DuckDB and BigQuery entity-key predicates) |
| graph | entity/edge drafting from schema, join-based traversal via `Warehouse` |
| policy | rule evaluation, sensitive-column suggestions, replay |
| monitor | schema/pattern/value drift, revisions, feedback summary (gap clustering) |
| serve | `Service` (the only path to data), MCP server, FastAPI JSON API + review UI |
| cli, evaluate, demo | entry points; eval arms: layer, baseline, baseline-hints (stops on LLM quota/transport errors) |

## Data model
- **Metric:** name, `metric_type` (`simple` | `ratio` | `derived`), concept, table, agg, column, `numerator`/`denominator` or `formula` (restricted arithmetic AST), filters, dimensions, `time_dimension`/`time_dimensions`, `synonyms`, entity + entity_key, owner, version, state, evidence
- **Entity / Edge:** table-backed entity (key column); edge `src.src_key = dst.dst_key` with a relationship name and `cardinality` (`one_to_many` | `many_to_one` | `one_to_one` | `many_to_many`)
- **Rule:** role, metric (or `*`), effect, row_filter, deny_dims
- **Evidence:** source queries, lineage, explanation, usage, users, suggested owner
- **States:** `draft → certified ↔ suspended → deprecated`; certified definitions are revised via a *pending* copy, which is diffed and re-certified (version +1); `semlayer canary` moves broken certified definitions to `suspended` (never served)

## Registry layout
```
definitions/metrics|entities|edges|rules/<name>.yaml   # live (draft/certified/suspended/deprecated)
definitions/_pending/<kind>/<name>.yaml                # revisions awaiting certification
definitions/_state/audit.jsonl                         # who did what (commit this)
definitions/_state/{index.db,usage.jsonl,...}          # local SQLite + FTS5 index, git-ignored
```

## Invariants
1. Agents get certified definitions only; drafts, pending revisions, suspended and deprecated ones are never served.
2. Every answer carries metric name, version and owner.
3. Policy is checked on every query (including every transitive dependency of `ratio`/`derived` metrics): default deny, certified rules only. When `serve --role` (`bound_role`) is set, caller-supplied `as_role` is rejected.
4. Dimensions and filters are limited to certified dimensions and unique shortest `many_to_one`/`one_to_one` edge joins (`one_to_many` fan-out joins are refused); values are bound parameters on DuckDB and validated `sqlglot` AST literal nodes on BigQuery MCP. A dimension pinned by an equality filter is not grouped.
5. Graph filters are limited to columns that certified metrics expose as dimensions, minus the role's denied dimensions; traversal needs an allow-all role.
6. LLMs and heuristics propose; humans certify (the `demo` command is the labelled exception).
7. YAML is the source of truth; the SQLite + FTS5 index can be deleted and rebuilt.
8. Eval traffic is not written to the usage log, sensitive column values are omitted from eval prompts, and LLM transport/quota errors stop an eval run without reporting a score.

## MCP tools
`list_metrics(limit, offset)`, `search_metrics(query, k)`, `describe_metric(name)`, `query_metric(metric, as_role, dims, filters, entity_keys, time_grain, time_range, order_by, limit)`, `query_metrics(metrics, ...)`, `describe_graph()`, `traverse_graph(start, path, as_role, filters)`, `report_gap(question, as_role, reason)`, `report_issue(metric, note, as_role)`. There is no raw-SQL tool.
