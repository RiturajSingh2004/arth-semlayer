# EVAL

Commands (run from the project root):
```
semlayer eval --oracle                                    # layer with the correct tool call supplied
semlayer eval --arm layer                                 # LLM routes through the certified layer
semlayer eval --arm baseline                              # LLM writes raw SQL from the schema, no governance
semlayer eval --arm baseline-hints                        # same, with column values listed in the prompt
semlayer eval --arm layer --questions eval/heldout.yaml   # held-out set
```
LLM arms need Ollama and `llm_model` in `semlayer.yaml`. Eval traffic is not written to the usage log.

## Question sets
- `eval/questions.yaml`: 33 questions (generator: `python eval/make_questions.py`). The prompt and compiler were tuned against this set.
- `eval/heldout.yaml`: 18 questions with different phrasing and values (generator: `python eval/make_heldout.py`). Not tuned against.

Categories: single, grouped, filtered, role, abstain, multi-hop. Each question has gold SQL against the raw tables, or an expected refusal.

## Arms
| Arm | What it tests | Needs |
|---|---|---|
| `layer --oracle` | layer correctness and governance, correct tool call supplied | nothing |
| `layer` | an LLM picks metric, dims, filters and graph call from the certified catalog | Ollama |
| `baseline` | an LLM writes raw SQL from the schema, no governance | Ollama |
| `baseline-hints` | as `baseline`, with the values of low-cardinality text columns in the prompt | Ollama |

The layer prompt includes metric descriptions, dimension values, the relationships list and a few examples from a different dataset. The baselines get no examples. Not built: an arm using uncertified drafts.

## Recorded results
Model: qwen3.5:9b (Q4_K_M), Ollama, RTX 3050 4 GB, `num_gpu` 25, temperature 0.

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

In the baseline rows, "overall" counts a SQL error on a should-refuse question as a pass, although the baseline never actually refuses.

Other results:
- **Agent test** (Claude Desktop through MCP, a much stronger model than the 9B): revenue, revenue by method, placed orders, returned-order revenue (via `describe_graph`, `traverse_graph`, `query_metric`), churn abstention with gap logged, role denial for `eu_analyst` revenue, EU-filtered customer count. The returned-order question failed until `describe_graph` existed, because no tool listed relationship names.
- **Drift test:** dropping `payments.method` opened a pending revision for `revenue` only; the live version kept serving; rejecting the revision restored a clean queue.
- **Feedback:** gap notes are clustered by question text (first sentence, word overlap); policy replay showed no unauthorized result.

## Findings
- Outputs are deterministic for identical inputs (the three held-out layer runs matched exactly), so repeated runs are not independent samples.
- Routing is sensitive to the catalog text: rewriting metric descriptions moved the tuned score from 25/25 to 21/25. Re-run the eval after any description, rule or metric change.
- Column-value hints help a raw-SQL baseline (filtered questions 5/8 to 8/8; held-out answers 5/13 to 8/13) but not with business definitions (revenue came out as 14,585.50 instead of 26,035.71), multi-hop questions (0 correct) or access control (0 real refusals).
- The layer's accuracy lead holds with hints equalised: +4 answers on the tuned set and +4 on the held-out set.
- Remaining layer failures: an unrequested grouping by `method` on multi-hop questions (totals correct, split by method); a transient Ollama HTTP 500 (retries added afterwards); `Orders by customer region` answered with a different metric (customers by region); `How many customers made a payment?` routed to `customer_count` plus a graph filter the layer refused.
- Baseline failures: wrong business definitions, unsupported date functions, nonexistent columns, wrong multi-hop joins, and no access control (7 of 8 refusal questions answered; the EU analyst saw all 60 customers).

## Caveats
- The prompt and compiler were tuned against the 33-question set; use the held-out numbers as the generalisation estimate. Samples are small (33 and 18).
- The layer prompt has examples that the baselines do not.
- Roles are asserted by the caller; the eval does not test authentication.
- Compiler rule added after seeing failures: a dimension pinned by an equality filter is not grouped.

## Rules
- Fixed model and temperature; re-run after any catalog or prompt change.
- Do not edit prompts after looking at held-out results; if you do, write a new held-out set.
- Report failures, not just wins.