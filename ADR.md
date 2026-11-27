# ADR

Format: Context / Decision / Consequences.

## ADR-001: Single Python package with subpackages (amended)
- Context: first plan was a uv workspace of one package per module; that adds packaging overhead with no benefit at this size.
- Decision: one package, `src/semlayer/<module>`; modules stay import-isolated by convention (no cycles).
- Consequences: simple build and tests; split into workspace packages later if a module needs its own release cycle.

## ADR-002: Minimal built-in compiler behind a `Compiler` protocol (amended)
- Context: wrapping MetricFlow needs a dbt project and heavy dependencies; the metrics we draft are single-table aggregates.
- Decision: ship `SimpleCompiler` (single table, filters, dimensions, entity keys); keep the protocol so MetricFlow or Cube can replace it.
- Consequences: no joins in metrics yet; compiler is small enough to audit.

## ADR-003: YAML registry with SQLite index
- Decision: YAML files are the source of truth (diffable, Git-committable via `--commit`); SQLite index is rebuildable; audit log is append-only JSONL.
- Consequences: free audit trail and PR-based review; concurrent writers should go through Git.

## ADR-004: MCP is the only agent interface; certified-only serving
- Decision: agents reach data only through `Service`/MCP; no SQL tool exists.
- Consequences: coverage gaps must abstain and call `report_gap`.

## ADR-005: Graph as foreign-key tables, no graph database
- Decision: entities map to tables; edges are key equalities inferred from `<x>_id` naming; traversal compiles to joins with bound parameters.
- Consequences: no server to run; naming-convention inference needs human certification.

## ADR-006: Provider-agnostic LLM, optional
- Decision: `LLM` protocol with Ollama backend; drafting works with no LLM (heuristics). The LLM only words descriptions/explanations.
- Consequences: weaker local models cannot corrupt logic; wording quality varies.

## ADR-007: Policy is human-authored, default deny
- Decision: only certified rules apply; deny beats allow; `row_filter` must be a plain predicate; suggestions are limited to dimension restrictions on sensitive columns and are never grants.
- Consequences: slower coverage, safer defaults; replay tests before certifying.

## ADR-008: SKILL.md is guidance only
- Decision: enforcement lives in the server, not in prose.

## ADR-009: Everything free and local; Apache-2.0
- Decision: open-source dependencies only; Docker Compose for local deploy.

## ADR-010: One FastAPI app for API and review UI
- Decision: JSON API plus HTMX review UI, no CSS framework; forms work without JavaScript (HTMX loads from a CDN).
- Consequences: basic look; PR-based review also works.

## ADR-011: Graph and policy were phase-gated
- Decision: built after metrics and eval; eval keeps multi-hop questions to justify the graph.

## ADR-012: Metric scope is single-table aggregates
- Decision: draft only `count`, `count distinct`, `sum`, `avg`, `min`, `max` over one table; queries with joins/subqueries are skipped and counted.
- Consequences: honest coverage numbers; joins are the main follow-up.

## ADR-013: Structural conflict detection, no embeddings (v1)
- Decision: cluster by concept name (query alias) and compare logic (table, expression, filters); also flag identical logic under different names.
- Consequences: no ML dependency; misses semantically similar metrics with different aliases.

## ADR-014: Graph traversal requires an allow-all role
- Context: traversal returns row-level keys that row filters would not cover.
- Decision: `traverse_graph` needs an allow rule on metric `*` with no row filter and no deny rule.
- Consequences: restricted roles cannot traverse; revisit with per-entity policy.

## ADR-015: `demo` auto-certifies
- Decision: the demo command certifies everything so the eval can run unattended; it prints a warning and is not the production path.


## ADR-016: Dimensions pinned by an equality filter are not grouped
- Context: models often grouped by the dimension they also filtered on, adding a constant column and failing shape comparison.
- Decision: the compiler drops a dimension from GROUP BY when it has an equality filter.
- Consequences: same totals however the call is phrased; does not fix unrequested groupings on other dimensions.

## ADR-017: Graph filters limited to certified dimensions; `describe_graph`
- Context: an agent could not discover relationship names, and `traverse_graph` accepted filters on any column (probing risk, e.g. email).
- Decision: `describe_graph` lists entities, filterable columns and relationship names; graph filters must be certified dimensions of metrics on that table and not denied to the role.
- Consequences: metadata only is exposed; filtering on uncertified columns is refused.

## ADR-018: Eval policy
- Decision: temperature 0; eval traffic is not logged; a held-out set is used once per prompt version; the eval is re-run after any description, rule or metric change.
- Consequences: results are deterministic for identical inputs, so repeated runs are not independent samples. Routing is sensitive to catalog text.

## ADR-019: Gap reports are clustered
- Context: agents wrote explanations into the question field, fragmenting counts.
- Decision: `report_gap(question, as_role, reason)` takes the question verbatim; the feedback summary clusters notes by first sentence and word overlap.
- Consequences: counts are approximate; similar-worded questions may merge.

## ADR-020: LLM client behaviour
- Decision: Ollama client retries transient 5xx and connection errors; routing prompts request JSON format; GPU layers come from `SEMLAYER_NUM_GPU`.
- Consequences: transient server errors no longer count as model failures.

## ADR-021: Rules are created as drafts through the CLI
- Decision: `rule-add` creates a draft rule (validated); `certify --kind rule` activates it; `reindex` rebuilds the index after manual file edits.
- Consequences: no manual YAML needed; a draft rule grants nothing.


## ADR-022: LLM on by default, provider-agnostic
- Context: semlayer is meant to run alongside LLMs; Ollama is the most common local engine, but users run others.
- Decision: the LLM is on by default (Ollama, `qwen3.5:9b`). `llm_provider: openai` covers any OpenAI-compatible server. Environment variables and `--no-llm` override the config. Drafting falls back to heuristic descriptions after the first connection failure; eval fails fast with a clear message.
- Consequences: a fresh checkout works without any LLM installed; CI sets `SEMLAYER_LLM_PROVIDER=none`; only the Ollama path has been exercised against a real server.

## ADR-023: `semlayer demo` protects existing registries
- Context: `demo` previously called `shutil.rmtree` on the entire registry root, silently destroying any real definitions a user had certified there, and also removing `.gitkeep` (causing a spurious git diff). The wipe also happened *after* the demo database was rebuilt, so the database could be replaced even if the wipe later failed.
- Decision: `demo` now calls `_reset_registry(root, force)` as its **first** action (before touching the database). The function refuses with a clear `SystemExit` if the registry contains YAML definitions that demo did not create (detected by the absence of `definitions/_state/demo.marker`). The guard can be overridden with `--force`. The wipe removes only the known subdirectories (`metrics/`, `entities/`, `edges/`, `rules/`, `_pending/`, `_state/`) and leaves `.gitkeep` and any other files in place. The marker is re-created after each successful run so that subsequent `demo` runs pass the check automatically.
- Consequences: running `demo` twice in a row is safe and idempotent; a user who has certified real definitions will get a clear error rather than silent data loss; `--force` is available when you genuinely want to wipe and restart; `.gitkeep` is preserved so git stays clean.

## ADR-024: Gemini API is the default LLM; local models stay as secondary options (amends ADR-009 and ADR-022)
- Context: local inference needs a large model download and suitable hardware. The Gemini API (AI Studio key) exposes an OpenAI-compatible endpoint (`https://generativelanguage.googleapis.com/v1beta/openai`) that works without adding an LLM SDK, independent of which GCP project hosts BigQuery.
- Decision: default to `llm_provider: gemini`, `llm_model: gemini-3.5-flash-lite`, key from `GEMINI_API_KEY` (loaded from environment or `.env`), with configurable `llm_max_tokens` (default 512) and `llm_reasoning_effort` (default `low`) so thinking tokens do not starve output. Keep `ollama` and `openai` as secondary local-model options, plus `none`. Reuse `OpenAICompatLLM`; add no LLM SDK. The LLM still only words descriptions and runs eval arms.
- Verified findings (Phase 7/8): `gemini-3.5-flash-lite` accepts `response_format: {"type": "json_object"}` and `reasoning_effort: "low"` together; `max_tokens: 512` produced zero empty replies across `demo` and all eval sets; pacing at `15 RPM` (`4.0s/request`) completed 200+ consecutive calls with zero `HTTP 429` errors.
- Consequences: setup needs an AI Studio `GEMINI_API_KEY` and network access; catalog text, filters and non-sensitive dimension values leave the machine, so sensitive column values are stripped from eval prompts and local `ollama`/`openai` remain for private data. Without a key, drafting falls back to heuristics and LLM eval stops with a clear message.

## ADR-025: Target GCP project and zero-surprise cost posture (amends ADR-009)
- Context: free allowances on a personal billing-enabled project can be exceeded and charged; lab-provisioned GCP projects (such as `geminisprints26blr-3913` under `devstar3913@gcplab.me`) or unbilled BigQuery sandboxes bound spend without linking a personal billing account.
- Decision: link no personal billing account; target GCP project `geminisprints26blr-3913` (overridable in `semlayer.yaml` or `SEMLAYER_BQ_PROJECT`). Run `semlayer` and its MCP server locally over stdio and keep the usage log as a local file (no Cloud Run, Artifact Registry, Secret Manager or Firestore).
- Verified findings (Phase 7): on lab project `geminisprints26blr-3913`, `devstar3913@gcplab.me` holds `roles/bigquery.admin` and `roles/editor` while `resourcemanager.projectIamAdmin` (`setIamPolicy`) is restricted to lab organisers; ADC (`gcloud auth login --update-adc`) + `bq load` (`scripts/bq_seed.py`) works directly, while user-owned projects can additionally bind a dedicated read-only service account (`bq_service_account`).
- Consequences: zero cloud hosting infrastructure to manage; exceeding lab or sandbox quotas fails closed instead of incurring personal charges; sandbox tables (if used on an unbilled project) can be re-seeded via `scripts/bq_seed.py`.

## ADR-026: BigQuery through Google's managed MCP server (extends ADR-004)
- Context: the goal is to integrate GCP through MCP. Alternatives: the native BigQuery client (query parameters, byte cap, no MCP on the warehouse side) and self-hosted MCP Toolbox (fixed tool templates fit dynamically compiled SQL poorly).
- Decision: `semlayer` is an MCP client of `https://bigquery.googleapis.com/mcp` (Streamable HTTP with `x-goog-user-project`) and calls only `execute_sql_readonly`. Agents connect to `semlayer` only, never to the BigQuery MCP server. Authenticate via Google ADC (refreshed via a stdlib `urllib.request` `google.auth.transport.Request` adapter so `requests` is not needed) or optional service-account impersonation (`bq_service_account`). DuckDB remains the default local warehouse; BigQuery is selected via `warehouse: bigquery` in config. `google-auth` goes in an optional `gcp` dependency group.
- Verified findings (Phase 7/10): `https://bigquery.googleapis.com/mcp` exposes 9 tools (`list_dataset_ids`, `get_dataset_info`, `list_table_ids`, `get_table_info`, `execute_sql_readonly`, `execute_sql`, `get_query_results`, `cancel_job`, `get_job`). `execute_sql_readonly` requires `projectId` and `query`, natively accepts `dryRun` (`boolean`) and `timeoutMs` (default `20000`), returns JSON in `content[0].text` (`schema.fields`, `totalRows`, `rows: [{"f": [{"v": ...}]}]`, `jobComplete`, `totalBytesProcessed`), and rejects DML/DDL with `"MCP execute_sql_readonly tool allows only SELECT statements."`.
- Consequences: certification and policy still apply to every query. Anyone holding their own BigQuery credentials can bypass `semlayer`, so agents must not be given any.

## ADR-027: Rendered literals and BigQuery SQL dialect via sqlglot (exception to the bound-parameter control)
- Context: `execute_sql_readonly` has no bound query-parameter input, and BigQuery GoogleSQL differs from DuckDB on table qualification (`project.dataset.table`), date arithmetic (`DATE_SUB(CURRENT_DATE, INTERVAL 30 DAY)` vs `CURRENT_DATE - 30`), and array membership (`IN UNNEST([...])` vs `list_contains(?, col)`).
- Decision: behind a `Warehouse` protocol (`query`, `schema`, `dialect`), `BigQueryMCP` parses DuckDB-authored SQL with `sqlglot`, rejects multi-statement/non-`SELECT` queries and null bytes, substitutes `?` placeholders on the AST with typed `sqlglot` literal nodes (allowed: string, int, finite float, bool, null, `datetime.date`, and flat lists of scalars; empty entity-key lists compile to `FALSE` to avoid untyped `UNNEST([])`), rewrites `CURRENT_DATE +/- N` to `DATE_SUB`/`DATE_ADD`, qualifies unqualified table references with `` `project.dataset.table` ``, and verifies the rendered SQL re-parses as a single `SELECT`. DuckDB keeps bound parameters.
- Consequences: injection safety on BigQuery relies on `sqlglot` AST literal rendering plus identifier validation, hostile-input tests, pinned `uv.lock`, and `execute_sql_readonly` + read-only IAM roles.

## ADR-028: Query safeguards on BigQuery MCP
- Context: Google's remote `execute_sql_readonly` MCP tool caps responses at 3,000 rows, defaults to a 20s wait (`timeoutMs`), and underlying BigQuery jobs can report errors, truncation (`pageToken` / `totalRows > len(rows)`), or `jobComplete: false`.
- Decision: inspect the `execute_sql_readonly` tool schema via `list_tools`; run a dry-run first via `execute_sql_readonly(..., dryRun=True)` (falling back to BigQuery's `jobs.query` REST `dryRun=true` if an alternative MCP server omits `dryRun`) and refuse above `bq_max_bytes`. Refuse if `jobComplete` is false, errors are present, or results are truncated (`pageToken` present or `totalRows` exceeds returned rows). Convert result cell strings using the returned BigQuery field types.
- Consequences: two round-trips per query when dry-run is enabled (`bq_max_bytes > 0`); oversized scans and partial/truncated traversals fail closed rather than returning silent partial results.

## ADR-029: Rate-limit backoff and eval integrity (amends ADR-020)
- Context: `_send` previously retried only 5xx and connection errors, so HTTP 429 raised immediately (`HTTPError` is an `OSError`). Drafting then disabled the LLM for the rest of the run, and `evaluate.py` caught `OSError` as `"error"`, which scored as a pass on `expect: refuse` questions.
- Decision: pace requests (`llm_rpm`), retry HTTP 429 with `Retry-After` (or exponential backoff), and abort an `eval` run immediately on any LLM transport or quota error without reporting a score. Strip values of sensitive columns (`policy.SENSITIVE`) from both `layer` and `baseline-hints` eval prompts.
- Consequences: slower runs under tight rate limits; an exhausted quota stops cleanly instead of inflating abstention scores.
