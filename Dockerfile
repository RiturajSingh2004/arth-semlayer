FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY . .
RUN uv sync --no-dev --frozen
ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["semlayer"]
CMD ["serve", "--web", "--host", "0.0.0.0"]
