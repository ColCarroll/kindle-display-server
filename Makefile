.PHONY: help test lint typecheck format check all clean install

help:  ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

install:  ## Install dependencies with uv
	uv pip install -e ".[dev]"

test:  ## Run tests with pytest
	pytest -v

test-cov:  ## Run tests with coverage report
	pytest -v --cov=app --cov-report=term-missing --cov-report=html

lint:  ## Run ruff linter
	ruff check app tests

lint-fix:  ## Run ruff linter and fix issues
	ruff check --fix app tests

format:  ## Format code with ruff
	ruff format app tests

format-check:  ## Check code formatting without making changes
	ruff format --check app tests

typecheck:  ## Run pyright type checker
	pyright app tests

check: lint format-check typecheck  ## Run all checks (lint, format, typecheck)

all: check test  ## Run all checks and tests

clean:  ## Clean up generated files
	rm -rf .pytest_cache
	rm -rf .ruff_cache
	rm -rf htmlcov
	rm -rf .coverage
	rm -rf dist
	rm -rf build
	rm -rf *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

.DEFAULT_GOAL := help
