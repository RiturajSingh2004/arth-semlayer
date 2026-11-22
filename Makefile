.PHONY: demo test lint eval web
demo:
	uv run semlayer demo
test:
	uv run pytest -q
lint:
	uv run ruff check src tests eval examples
eval:
	uv run semlayer eval --oracle
web:
	uv run semlayer serve --web
