# ARTH: Semantic Layer

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/RiturajSingh2004/arth-semlayer/blob/main/notebooks/certified_bigquery_mcp.ipynb)

Repository: `arth-semlayer` · Python package and command: `semlayer`

Governed semantic layer between AI agents and your data. Definitions are drafted automatically from your schema and query log, humans certify them, and agents can only use certified ones.

- **GCP-integrated + local fallback:** Gemini API default LLM (`ollama`/`openai` secondary local options), DuckDB local default warehouse + Google's managed BigQuery MCP server (`https://bigquery.googleapis.com/mcp`)
- **Loop:** ingest → draft → find conflicts → certify → serve over MCP → monitor drift
- **Agents never touch the warehouse.** They call `semlayer`'s MCP server; certification and policy are enforced there.

## Quickstart (local DuckDB)
```bash
uv sync
uv run semlayer demo          # seeds DuckDB + query log, runs the loop, auto-certifies (DEMO ONLY)
uv run semlayer eval --oracle # 33-question eval of the certified layer (no LLM key needed)
uv run semlayer serve --web   # review UI + JSON API on :8000
uv run semlayer serve         # MCP server (stdio) for agents
uv run pytest                 # unit + fake-server suite (runs offline with no keys)
```
Run every command from the project root. Local prerequisites: [uv](https://docs.astral.sh/uv/) (DuckDB and SQLite are bundled).

## LLM configuration (Gemini default, local models secondary)
`semlayer` uses an LLM for two things only: wording metric descriptions/explanations during `draft`, and running the LLM `eval` arms. It never changes metric SQL logic.

### 1. Default: Gemini API via AI Studio (`gemini-3.5-flash-lite`)
Put your AI Studio `GEMINI_API_KEY` in `.env` (git-ignored) or your environment. `semlayer.yaml` defaults to:
```yaml
llm_provider: gemini                 # gemini | ollama | openai | none
llm_model: gemini-3.5-flash-lite
llm_max_tokens: 512
llm_reasoning_effort: low
```
`GEMINI_API_KEY` authenticates directly against `https://generativelanguage.googleapis.com/v1beta/openai` and works alongside GCP Application Default Credentials used by `BigQueryMCP`.

### 2. Secondary option: Local models (`ollama` or `openai`)
Switch to local inference with config changes only in `semlayer.yaml`:
```yaml
llm_provider: ollama                 # or "openai" for llama.cpp (:8080/v1), LM Studio (:1234/v1), vLLM
llm_model: qwen3.5:9b
# llm_url: http://localhost:11434
```
Override without editing files via `SEMLAYER_LLM_PROVIDER`, `SEMLAYER_LLM_MODEL`, `SEMLAYER_LLM_URL`, or disable the LLM with `--no-llm` / `SEMLAYER_LLM_PROVIDER=none`. Without a key or running local server, `draft` and `demo` warn once and fall back to heuristic descriptions; LLM `eval` fails fast or aborts cleanly on quota errors (`ADR-029`).

## GCP & BigQuery over MCP
Install the optional GCP auth dependency and sign into your GCP project:
```bash
uv sync --extra gcp
gcloud auth login --update-adc
gcloud config set project YOUR_PROJECT_ID
gcloud auth application-default set-quota-project YOUR_PROJECT_ID
gcloud services enable bigquery.googleapis.com iamcredentials.googleapis.com --project=YOUR_PROJECT_ID
gcloud beta services mcp enable bigquery.googleapis.com --project=YOUR_PROJECT_ID
```
Seed the `jaffle_shop` tables into BigQuery and verify over MCP:
```bash
uv run python scripts/bq_seed.py --project YOUR_PROJECT_ID --dataset jaffle_shop
SEMLAYER_WAREHOUSE=bigquery SEMLAYER_BQ_PROJECT=YOUR_PROJECT_ID uv run semlayer eval --oracle
SEMLAYER_BQ_PROJECT=YOUR_PROJECT_ID uv run python scripts/mcp_check.py --warehouse bigquery
```
Or set `warehouse: bigquery` (and optional `bq_service_account` for read-only service-account impersonation) in `semlayer.yaml`.

## Real workflow (humans in the loop)
```bash
uv run python examples/jaffle_shop/seed.py
uv run semlayer ingest
uv run semlayer draft --roles finance,analyst
uv run semlayer review                          # ranked queue (by demand)
uv run semlayer conflicts                       # variants / duplicates with ranked options
uv run semlayer conflicts --split active_customers --names a_v1=active_30d a_v2=active_paid
uv run semlayer certify revenue --accept-suggested
uv run semlayer rule-add finance_all --role finance
uv run semlayer rule-add eu_customers --role eu_analyst --metric customer_count --row-filter "region = 'EU'"
uv run semlayer certify finance_all --kind rule --owner security
uv run semlayer edit revenue entity=Customer entity_key=customer_id
uv run semlayer drift --values
uv run semlayer feedback
```

## Integrations & Examples
- **Interactive Colab / Vertex AI Notebook:** [`notebooks/certified_bigquery_mcp.ipynb`](notebooks/certified_bigquery_mcp.ipynb) ([Open in Colab](https://colab.research.google.com/github/RiturajSingh2004/arth-semlayer/blob/main/notebooks/certified_bigquery_mcp.ipynb)) — self-contained walkthrough connecting Gemini (`google-genai` on Vertex AI) to the governed MCP server over BigQuery MCP with an offline DuckDB toggle.
- **Google ADK Agent (`McpToolset` + FastAPI / Cloud Run):** [`examples/adk_agent/`](examples/adk_agent/README.md) — standalone deployable ADK `LlmAgent` wired to the governed stdio MCP server with Dockerfile and 27 offline tests (`cd examples/adk_agent && uv sync --dev && uv run pytest`).

## Layout
```
src/semlayer/  core warehouse ingest lineage draft conflicts registry compiler graph policy monitor serve cli evaluate demo
examples/jaffle_shop/   seed.py (data + query log with deliberate conflicts)
examples/adk_agent/     Google ADK LlmAgent + governed MCP server + Dockerfile
notebooks/              certified_bigquery_mcp.ipynb (Vertex AI / Colab walkthrough)
eval/                   questions.yaml, heldout.yaml (+ generators)
definitions/            the registry (YAML, Git-friendly)
scripts/                manual_run.sh, mcp_check.py, bq_seed.py
skills/semlayer/        SKILL.md for agents
tests/
```

## Docs
PLAN.md · ARCHITECTURE.md · ADR.md · EVAL.md · SECURITY.md · GEMINI.md

<!-- mcp-name: io.github.RiturajSingh2004/arth-semlayer -->
