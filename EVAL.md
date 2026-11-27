# EVAL

Commands executed (from the project root in PowerShell):
```powershell
# 1. Seed & certify local registry with Gemini descriptions (GEMINI_API_KEY in .env)
uv run semlayer demo --force

# 2. Oracle & Gemini LLM evals on DuckDB
uv run semlayer eval --oracle
uv run semlayer eval --arm layer
uv run semlayer eval --arm baseline-hints
uv run semlayer eval --arm baseline
uv run semlayer eval --arm layer --questions eval/heldout.yaml
uv run semlayer eval --arm baseline-hints --questions eval/heldout.yaml
uv run semlayer eval --arm baseline --questions eval/heldout.yaml

# 3. Seed BigQuery & run Oracle + Gemini LLM evals over Google's managed BigQuery MCP server
gcloud auth login devstar3913@gcplab.me --update-adc
gcloud config set project geminisprints26blr-3913
gcloud auth application-default set-quota-project geminisprints26blr-3913
gcloud services enable bigquery.googleapis.com iamcredentials.googleapis.com --project=geminisprints26blr-3913
uv run python scripts/bq_seed.py --project geminisprints26blr-3913 --dataset jaffle_shop

$env:SEMLAYER_WAREHOUSE = "bigquery"
$env:SEMLAYER_BQ_PROJECT = "geminisprints26blr-3913"
$env:SEMLAYER_BQ_DATASET = "jaffle_shop"
uv run semlayer eval --oracle
uv run python scripts/mcp_check.py --warehouse bigquery
uv run semlayer eval --arm layer
uv run semlayer eval --arm layer --questions eval/heldout.yaml
```
Eval traffic is not written to the usage log, values of sensitive columns (`policy.SENSITIVE`) are stripped from prompts, and any LLM transport or quota error aborts the run without reporting a score (`ADR-029`).

## Question sets
- `eval/questions.yaml`: 33 questions (generator: `python eval/make_questions.py`). The prompt and compiler were tuned against this set.
- `eval/heldout.yaml`: 18 questions with different phrasing and values (generator: `python eval/make_heldout.py`). Not tuned against.

Categories: single, grouped, filtered, role, abstain, multi-hop. Each question has gold SQL against the raw tables, or an expected refusal.

## Arms
| Arm | What it tests | Needs |
|---|---|---|
| `layer --oracle` | layer correctness, SQL dialect translation, and governance with the tool call supplied | DuckDB or BigQuery MCP |
| `layer` | an LLM picks metric, dims, filters and graph call from the certified catalog | Gemini API (default) or Ollama |
| `baseline` | an LLM writes raw SQL from the schema, no governance | Gemini API or Ollama |
| `baseline-hints` | as `baseline`, with values of low-cardinality non-sensitive text columns in the prompt | Gemini API or Ollama |

---

## Recorded results — Gemini API (`gemini-3.5-flash-lite`) + DuckDB & BigQuery MCP
- **LLM:** `gemini-3.5-flash-lite` via AI Studio OpenAI-compatible endpoint (`https://generativelanguage.googleapis.com/v1beta/openai`), `temperature: 0`, `reasoning_effort: low`, `max_tokens: 512`, `15 RPM` pacing (`4.0s/request`), Gemini-drafted catalog descriptions.
- **Cloud warehouse:** Google managed BigQuery MCP (`https://bigquery.googleapis.com/mcp`, `execute_sql_readonly` with native `dryRun: true`), GCP project `geminisprints26blr-3913`, dataset `jaffle_shop`.

| Set | Warehouse | Arm | Answers | Real refusals | Abstention precision | Wrong answers | Overall | Latency / q |
|---|---|---|---|---|---|---|---|---|
| Tuned (33) | DuckDB | `layer --oracle` | 25/25 | 8/8 | 8/8 | 0 | **33/33** | 0.0s |
| Tuned (33) | BigQuery MCP | `layer --oracle` | 25/25 | 8/8 | 8/8 | 0 | **33/33** | 6.9s |
| Tuned (33) | DuckDB | `layer` (Gemini) | 25/25 | 8/8 | 8/8 | 0 | **33/33** | 4.0s |
| Tuned (33) | BigQuery MCP | `layer` (Gemini) | 25/25 | 8/8 | 8/8 | 0 | **33/33** | 8.3s |
| Tuned (33) | DuckDB | `baseline-hints` (Gemini) | 22/25 | 0/8 | n/a (0 refusals) | 3 | **22/33** | 4.0s |
| Tuned (33) | DuckDB | `baseline` (Gemini) | 15/25 | 0/8 | n/a (0 refusals) | 9 (+1 SQL err) | **15/33** | 4.0s |
| Held-out (18) | DuckDB | `layer` (Gemini) | 13/13 | 4/5 | 4/4 | 0 | **17/18** | 4.0s |
| Held-out (18) | BigQuery MCP | `layer` (Gemini) | 12/13 | 5/5 | 5/6 | 0 | **17/18** | 8.5s |
| Held-out (18) | DuckDB | `baseline-hints` (Gemini) | 11/13 | 0/5 | n/a (0 refusals) | 2 | **11/18** | 4.0s |
| Held-out (18) | DuckDB | `baseline` (Gemini) | 6/13 | 0/5 | n/a (0 refusals) | 7 | **6/18** | 4.0s |

---

## Secondary baseline — Local Ollama (`qwen3.5:9b`) on DuckDB
Model: `qwen3.5:9b` (Q4_K_M), Ollama, RTX 3050 4 GB, `num_gpu` 25, temperature 0.

| Set | Arm | Answers | Real refusals | Overall |
|---|---|---|---|---|
| Tuned (33) | layer, oracle routing | 25/25 | 8/8 | 33/33 |
| Tuned (33) | layer, LLM routing, first catalog | 25/25 | 8/8 | 33/33 |
| Tuned (33) | layer, LLM routing, rewritten descriptions | 21/25 | 8/8 | 29/33 |
| Tuned (33) | baseline-hints | 17/25 | 0/8 | 18/33 |
| Tuned (33) | baseline | 13/25 | 0/8 | 14/33 |
| Held-out (18) | layer, LLM routing | 12/13 | 4/5 | 16/18 |
| Held-out (18) | baseline-hints | 8/13 | 0/5 | 8/18 |
| Held-out (18) | baseline | 5/13 | 0/5 | 5/18 |

In the Qwen baseline rows, "overall" counted a SQL error on a should-refuse question as a pass, although the baseline never actually refused.

---

## Live GCP & MCP Verification Summary
- **End-to-end stdio → BigQuery MCP (`scripts/mcp_check.py --warehouse bigquery`):** Verified `list_metrics`, `describe_graph`, `query_metric("revenue", as_role="finance")` (`26035.71`), `query_metric("revenue", as_role="eu_analyst")` (`access denied`), `query_metric("customer_count", as_role="eu_analyst")` (`24`, EU row filter applied), and `traverse_graph("Customer", ["HAS_ORDER"], filters={"Order": {"status": "returned"}})` (`26` keys).
- **Hostile SQL-injection & read-only guardrails on live BigQuery MCP:** Filter payloads (`"card' OR '1'='1"`, `"card'; DROP TABLE payments; --"`, `"card\\' OR TRUE /* comment */"`) rendered through `sqlglot` AST literals returned `[[None]]` with zero leakage; `execute_sql_readonly(..., dryRun=True)` returned `totalBytesProcessed: 2400`; a direct `DELETE` probe against `execute_sql_readonly` was rejected server-side (`"MCP execute_sql_readonly tool allows only SELECT statements."`).

## Findings
- **Stronger models do not replace governance:** Upgrading raw text-to-SQL from `qwen3.5:9b` to `gemini-3.5-flash-lite` improved `baseline-hints` answer accuracy (`17/25` → `22/25` tuned; `8/13` → `11/13` held-out), yet **real refusals stayed at `0/8` and `0/5`**. Raw-SQL Gemini leaked `customers.email`, answered `guest` and `eu_analyst` unauthorized queries, invented formulas for `churn`, `gross_margin`, and `NPS`, and joined `payments.order_id = orders.id` (`14,585.50`) instead of customer-level entity semantics (`25,415.96`).
- **Gemini fixes the 9B routing drop on rewritten descriptions:** With LLM-authored descriptions, `gemini-3.5-flash-lite` achieved **`33/33` (`100%`)** on the tuned set on both DuckDB and BigQuery MCP (vs. `29/33` on `qwen3.5:9b`) and **`17/18`** on the held-out set (vs. `16/18` on `qwen3.5:9b`).
- **Zero silent wrong answers on the certified layer:** Across both held-out runs (DuckDB and BigQuery MCP), the single miss on each run was either routing an ambiguous refusal (`Orders by customer region` → `customer_count` by `region` on DuckDB) or a conservative layer refusal when Gemini passed a redundant `{"status": "success"}` filter to `avg_payment` on BigQuery MCP (`wrong_answers = 0`). As noted in `ADR-024`, cloud inference is not bit-identical across runs even at `temperature: 0`.
- **Drift & feedback:** Dropping `payments.method` opened a pending revision for `revenue` only while keeping the live version serving; gap notes cluster by question text and policy replay confirmed zero unauthorized access.

## Caveats & Rules
- The prompt and compiler were tuned against the 33-question set; use the 18-question held-out set as the generalisation estimate.
- Roles are asserted by the caller (`as_role`); wire authentication in front of MCP/HTTP in production.
- Fixed model and temperature; re-run after any catalog or prompt change; do not edit prompts after looking at held-out results; report failures, not just wins.
