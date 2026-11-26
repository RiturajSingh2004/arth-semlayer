# ARTH: Semantic Layer

Repository: `arth-semlayer` · Python package and command: `semlayer`

Governed semantic layer between AI agents and your data. Definitions are drafted automatically from your schema and query log, humans certify them, and agents can only use certified ones.

- **Free:** open-source stack, runs locally, no cloud bills (Apache-2.0)
- **Loop:** ingest → draft → find conflicts → certify → serve over MCP → monitor drift
- **Agents never touch the warehouse.** They call the MCP server; policy is enforced there.

## Quickstart
```bash
uv sync
uv run semlayer demo          # seeds DuckDB + query log, runs the loop, auto-certifies (DEMO ONLY)
uv run semlayer eval --oracle # 33-question eval of the certified layer (no LLM needed)
uv run semlayer serve --web   # review UI + JSON API on :8000
uv run semlayer serve         # MCP server (stdio) for agents
uv run pytest                 # 53 tests
```
Run every command from the project root. The only prerequisite is [uv](https://docs.astral.sh/uv/); DuckDB and SQLite come with the Python dependencies.

## Real workflow (humans in the loop)
```bash
uv run python examples/jaffle_shop/seed.py      # or point semlayer.yaml at your DuckDB + query log
uv run semlayer ingest
uv run semlayer draft --roles finance,analyst
uv run semlayer review                          # ranked queue (by demand)
uv run semlayer conflicts                       # variants / duplicates with ranked options
uv run semlayer conflicts --split active_customers --names a_v1=active_30d a_v2=active_paid
uv run semlayer certify revenue --accept-suggested   # or --owner NAME; owner is required
uv run semlayer rule-add finance_all --role finance            # draft access rule (default is deny)
uv run semlayer rule-add eu_customers --role eu_analyst --metric customer_count --row-filter "region = 'EU'"
uv run semlayer certify finance_all --kind rule --owner security
uv run semlayer edit revenue entity=Customer entity_key=customer_id   # revisions go through review
uv run semlayer drift --values                  # schema/pattern/value drift -> pending revisions
uv run semlayer feedback                        # agent demand, gaps, flagged metrics
```
`bash scripts/manual_run.sh` runs the whole manual flow. `bash scripts/manual_run.sh` runs the whole manual flow (needs bash). If you edit definition files by hand, run `semlayer reindex`.

## LLM (on by default)
semlayer uses a local LLM for two things: writing metric descriptions and explanations when drafting, and the LLM eval arms. It never changes metric logic. Agents that query semlayer over MCP bring their own model.

Default: Ollama with `qwen3.5:9b`.
```bash
ollama pull qwen3.5:9b      # about 6.6 GB; for less hardware pick a smaller model and set llm_model
```
Any other local engine works through its OpenAI-compatible API. In `semlayer.yaml`:
```yaml
llm_provider: openai
llm_model: <model name your server serves>
llm_url: http://localhost:8080/v1    # llama.cpp server; LM Studio: :1234/v1; vLLM: :8000/v1
# llm_api_key_env: MY_API_KEY        # only if your server needs a key
```
Override without editing the file: `SEMLAYER_LLM_PROVIDER`, `SEMLAYER_LLM_MODEL`, `SEMLAYER_LLM_URL`. Turn it off with `llm_provider: none`, `SEMLAYER_LLM_PROVIDER=none` or `semlayer --no-llm ...`.

If the server is not running, `draft` and `demo` print a warning and fall back to heuristic descriptions; `eval --arm layer|baseline|baseline-hints` stops with a clear message.

Ollama only: `SEMLAYER_NUM_GPU` sets the GPU layers (default 25, tuned for a 4 GB card with the 9B model). Lower it if you see CUDA errors.

## Layout
```
src/semlayer/  core ingest lineage draft conflicts registry compiler graph policy monitor serve cli evaluate demo
examples/jaffle_shop/   seed.py (data + query log with deliberate conflicts)
eval/                   questions.yaml, heldout.yaml (+ generators)
definitions/            the registry (YAML, Git-friendly)
scripts/                manual_run.sh
skills/semlayer/        SKILL.md for agents
tests/
```

## Docs
PLAN.md · ARCHITECTURE.md · ADR.md · EVAL.md · SECURITY.md · CLAUDE.md

## Scope and limits (v0.1)
- Metrics are single-table aggregates (count, count distinct, sum, avg, min, max); queries with joins are skipped when drafting.
- Built-in minimal SQL compiler (see ADR-002). A dimension pinned by an equality filter is not grouped. A MetricFlow/Cube adapter is a planned swap.
- Warehouse: DuckDB. Other engines need a connection adapter.
- Roles are asserted by the caller and the review UI has no login (see SECURITY.md).
