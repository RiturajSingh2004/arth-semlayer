---
name: semlayer
description: Use when answering data questions through the semlayer MCP server. Defines how to use certified metrics, cite versions, and abstain correctly.
---

# semlayer usage

1. Call `list_metrics` (and `describe_metric`) before answering any data question.
2. Answer only with `query_metric`. Never write raw SQL against the warehouse; there is no SQL tool.
3. Cite the metric name and version in every answer.
4. If no certified metric matches, say so, call `report_gap` with the user's question verbatim (put any explanation in `reason`), and abstain. Do not guess or approximate.
5. If several metrics could fit (e.g. `active_customers_30d` vs `active_customers_paid`), ask which one is meant.
6. Pass the caller's role in `as_role`; never claim a broader role.
7. For questions about related entities ("revenue from customers who have a returned order"): call `describe_graph` for entity and relationship names, then `traverse_graph`, then pass the returned keys as `entity_keys` to `query_metric` (use a metric whose `entity` matches the start entity).
8. If a result looks wrong, call `report_issue`; do not work around the layer.
