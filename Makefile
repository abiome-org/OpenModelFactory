.PHONY: install format lint typecheck test test-all build release-candidate hooks

install:
	python3 -m pip install --only-binary=:all: --require-hashes -r requirements.lock
	python3 -m pip install --only-binary=:all: --require-hashes -r requirements.build.lock
	python3 -m pip install --no-build-isolation --no-deps -e .

format:
	python3 -m ruff format factory tests modules/examples
	python3 -m ruff check --fix factory tests modules/examples

lint:
	python3 -m ruff check factory tests tools modules/examples
	python3 -m ruff format --check factory tests tools modules/examples
	python3 tools/check_no_comments.py factory tests tools modules/examples

hooks:
	printf '#!/bin/sh\nexec make lint\n' > .git/hooks/pre-commit
	chmod +x .git/hooks/pre-commit

typecheck:
	python3 -m mypy

test:
	python3 -m pytest --cov=omf --cov-report=term

test-all: lint typecheck test

build:
	python3 -m build --no-isolation

release-candidate:
	python3 tools/release.py --candidate --output dist \
		--source-revision "$$(git rev-parse HEAD)" \
		--source-date-epoch "$$(git show -s --format=%ct HEAD)"
