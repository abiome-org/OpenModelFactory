.PHONY: install format lint typecheck test test-all build

install:
	python3 -m pip install --require-hashes -r requirements.lock
	python3 -m pip install --no-deps -e .

format:
	python3 -m ruff format factory tests modules/examples
	python3 -m ruff check --fix factory tests modules/examples

lint:
	python3 -m ruff check factory tests modules/examples
	python3 -m ruff format --check factory tests modules/examples

typecheck:
	python3 -m mypy

test:
	python3 -m pytest --cov=omf --cov-report=term

test-all: lint typecheck test

build:
	python3 -m build
