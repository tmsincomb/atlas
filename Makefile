.PHONY: setup test lint fmt docs docs-serve

setup:
	pip install -e ".[dev]"
	pre-commit install

test:
	python3 -m pytest -q

lint:
	ruff check .
	ruff format --check .
	mypy src/atlas

fmt:
	ruff format .
	ruff check --fix .

docs:
	uv run --group docs mkdocs build --strict

docs-serve:
	uv run --group docs mkdocs serve
