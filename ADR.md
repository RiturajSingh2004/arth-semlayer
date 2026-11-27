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

## ADR-023: `semlayer demo` protects existing registries
- Context: `demo` previously called `shutil.rmtree` on the entire registry root, silently destroying any real definitions a user had certified there, and also removing `.gitkeep` (causing a spurious git diff). The wipe also happened *after* the demo database was rebuilt, so the database could be replaced even if the wipe later failed.
- Decision: `demo` now calls `_reset_registry(root, force)` as its **first** action (before touching the database). The function refuses with a clear `SystemExit` if the registry contains YAML definitions that demo did not create (detected by the absence of `definitions/_state/demo.marker`). The guard can be overridden with `--force`. The wipe removes only the known subdirectories (`metrics/`, `entities/`, `edges/`, `rules/`, `_pending/`, `_state/`) and leaves `.gitkeep` and any other files in place. The marker is re-created after each successful run so that subsequent `demo` runs pass the check automatically.
- Consequences: running `demo` twice in a row is safe and idempotent; a user who has certified real definitions will get a clear error rather than silent data loss; `--force` is available when you genuinely want to wipe and restart; `.gitkeep` is preserved so git stays clean.

## ADR-024: Gemini API is the default LLM; Ollama stays as an option (proposed; amends ADR-009 and ADR-022)
- Context: local inference needs a large model download and suitable hardware. The Gemini API (AI Studio key) lists free-of-charge models and has an OpenAI-compatible endpoint.
- Decision: default to `llm_provider: gemini`, `llm_model: gemini-3.5-flash-lite`, key from `GEMINI_API_KEY`. Reuse `OpenAICompatLLM`; add no SDK. Keep `ollama`, `openai` and `none`. The LLM still only words descriptions and runs eval arms.
- Consequences: setup needs a key and network access; catalog text, filters and dimension values leave the machine, and the unpaid tier uses them to improve Google products and allows human review, so only demo data may be sent. Without a key, drafting falls back to heuristics and LLM eval stops with a message. Recorded Qwen results do not carry over, and identical outputs across runs are not assumed (revisits ADR-018).

## ADR-025: No billing account (proposed; amends ADR-009)
- Context: free allowances on a billing-enabled project can be exceeded and charged; budget alerts do not stop spending.
- Decision: link no billing account to any project. Use only the BigQuery sandbox and the Gemini API free tier. A new service must work without billing to be added.
- Consequences: exceeding a limit fails the request instead of costing money. No Cloud Run, so semlayer and its MCP server run locally over stdio and the usage log stays a local file. Sandbox tables expire after 60 days, DML and streaming are unavailable, and storage is capped at 10 GiB over the sandbox's lifetime.

## ADR-026: BigQuery through Google's managed MCP server (proposed; extends ADR-004)
- Context: the goal is to integrate GCP through MCP. Alternatives: the native BigQuery client (query parameters, byte cap, no MCP on the warehouse side) and self-hosted MCP Toolbox (fixed tool templates fit dynamically compiled SQL poorly).
- Decision: semlayer is an MCP client of `https://bigquery.googleapis.com/mcp` and calls only `execute_sql_readonly`. Agents connect to semlayer only, never to the BigQuery MCP server. Authenticate as a dedicated read-only service account. DuckDB remains the default; BigQuery is selected in config. `google-auth` goes in an optional `gcp` group.
- Consequences: certification and policy still apply to every query. Anyone holding their own BigQuery credentials can bypass semlayer, so agents must not be given any. Depends on the server's tool schema and limits (see ADR-027 and ADR-028).

## ADR-027: Rendered literals on BigQuery MCP (proposed; exception to the bound-parameter control)
- Context: the `execute_sql_readonly` input schema has no query parameters.
- Decision: the BigQuery warehouse replaces placeholders on the parsed syntax tree with sqlglot literal nodes. Allowed values: string, integer, finite float, boolean, null, date, and flat lists of these. Anything else is refused. No string formatting of values. DuckDB keeps bound parameters.
- Consequences: injection safety on BigQuery depends on sqlglot's escaping, which is weaker than binding; it needs hostile-input tests, a pinned version in `uv.lock`, and the read-only IAM role. If the tests cannot be made to pass, switch queries to the native client in a new ADR.

## ADR-028: Query safeguards on BigQuery MCP (proposed)
- Context: the tool has no maximum-bytes-billed input, results are capped at 3,000 rows (graph traversal allows 10,000 keys), and a query can return before the job completes.
- Decision: dry run first and refuse above a configured byte limit. Refuse if the job is incomplete, reports errors, or the result is truncated; page instead only if the server's tools support it.
- Consequences: two calls per query; large traversals are refused rather than answered partially.

## ADR-029: Free-tier rate limits and eval integrity (proposed; amends ADR-020)
- Context: the client retries only 5xx and connection errors, so HTTP 429 raises at once. Drafting then disables the LLM for the rest of the run, and eval scores the error as a pass on should-refuse questions.
- Decision: pace requests, back off on 429, and stop an eval run on any LLM transport or quota error. A stopped run reports no score.
- Consequences: slower runs; an exhausted daily quota means running again after it resets.
