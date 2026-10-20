.DEFAULT_GOAL := help

setup: ## install the package with development dependencies
	pip install -e .[dev]

lint: ## run linter
	ruff check sanic_session tests example

format: ## format code
	ruff format sanic_session tests example

test: ## run tests (memcached tests need memcached on 127.0.0.1:11211)
	pytest -v tests

build: ## build sdist and wheel into dist/
	python -m pip install --upgrade build
	python -m build

help: ## Display this help screen
	@grep -h -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'
