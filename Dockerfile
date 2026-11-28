FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.6.6 /uv /uvx /bin/

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    SEMLAYER_LOG_JSON=1 \
    PORT=8080

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev --extra gcp

COPY semlayer.yaml ./
COPY definitions ./definitions
COPY examples/jaffle_shop ./examples/jaffle_shop

RUN SEMLAYER_LLM_PROVIDER=none uv run --no-dev semlayer --no-llm demo --force

RUN useradd --create-home --uid 10001 semlayer && chown -R semlayer:semlayer /app
USER semlayer

EXPOSE 8080

CMD ["sh", "-c", "uv run --no-dev semlayer serve --web --host 0.0.0.0 --port ${PORT:-8080}"]
