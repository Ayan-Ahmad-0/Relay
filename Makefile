.PHONY: help install lint format test test-cov gitleaks migrate migrate-new clean

PYTHON ?= uv run python
PYTEST ?= uv run pytest
RUFF ?= uv run ruff
DBMATE ?= dbmate

help:
	@echo "Relay Development Commands:"
	@echo "  make install      - Install all dependencies with uv"
	@echo "  make lint         - Run ruff linter and format check"
	@echo "  make format       - Auto-format code with ruff"
	@echo "  make test         - Run test suite with pytest"
	@echo "  make test-cov     - Run tests with coverage"
	@echo "  make gitleaks     - Run secrets scanner"
	@echo "  make migrate      - Run dbmate migrations"
	@echo "  make migrate-new NAME=<name> - Create new dbmate migration"

install:
	uv sync --extra dev

lint:
	$(RUFF) check .
	$(RUFF) format --check .

format:
	$(RUFF) check --fix .
	$(RUFF) format .

test:
	$(PYTEST)

test-cov:
	$(PYTEST) --cov=relay --cov-report=term-missing

gitleaks:
	gitleaks detect --source . --verbose

migrate:
	$(DBMATE) up

clean:
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage
