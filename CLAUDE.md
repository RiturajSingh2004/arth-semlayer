# CLAUDE.md

ARTH: SemLayer is built (see PLAN.md status). Read ARCHITECTURE.md, ADR.md and SECURITY.md before changing behaviour.

## Rules
- Keep code minimal and idiomatic; no speculative abstractions.
- Python 3.12, uv, ruff, pytest. Pydantic models.
- Only free, open-source dependencies (ADR-009). Ask before adding any.
- Never serve drafts, pending revisions or deprecated definitions to agents (invariant 1).
- Every behaviour change needs a test; run `make lint test` before finishing.
- New decisions go in ADR.md as a new numbered entry; amend, do not rewrite history.
- Commit small, one concern per commit.

## Commands
```
uv sync
make demo test lint eval
uv run semlayer --help
```

## Extension points
- `Compiler` protocol (`compiler/`): add a MetricFlow/Cube adapter.
- `LLM` protocol (`core/llm.py`): add providers.
- `ingest/`: new connectors; the warehouse is DuckDB today.
- Next candidates: joins in metrics, embedding clustering, eval arm 3, per-entity graph policy.
