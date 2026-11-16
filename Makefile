# IncidentForge — Makefile
# Requires: uv, node, docker, kind, kubectl (run `make doctor` to check)

SHELL := /bin/bash
.DEFAULT_GOAL := help

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT         := $(shell pwd)
API_DIR      := $(ROOT)/services/api
WEB_DIR      := $(ROOT)/apps/web
VENV         := $(ROOT)/.venv
PYTHON       := $(VENV)/bin/python
PYTEST       := $(VENV)/bin/pytest
RUFF         := $(VENV)/bin/ruff

# PYTHONPATH lets both `contracts/` (at root) and `app/` (in services/api/) resolve.
PYTHONPATH   := $(ROOT):$(API_DIR)
export PYTHONPATH

# ── Colours ──────────────────────────────────────────────────────────────────
BOLD  := \033[1m
RESET := \033[0m
GREEN := \033[32m
RED   := \033[31m
CYAN  := \033[36m

# ─────────────────────────────────────────────────────────────────────────────
.PHONY: help
help: ## Show this help
	@printf "$(BOLD)IncidentForge$(RESET)\n\n"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  $(CYAN)%-22s$(RESET) %s\n", $$1, $$2}'

# ── Prerequisite check ───────────────────────────────────────────────────────
.PHONY: doctor
doctor: ## Check all prerequisites (docker, kubectl, kind, python, node, ports)
	@printf "$(BOLD)Checking prerequisites…$(RESET)\n"
	@command -v docker   >/dev/null 2>&1 && printf "  $(GREEN)✓$(RESET) docker\n"    || printf "  $(RED)✗ docker$(RESET) — install Docker Desktop\n"
	@command -v kubectl  >/dev/null 2>&1 && printf "  $(GREEN)✓$(RESET) kubectl\n"   || printf "  $(RED)✗ kubectl$(RESET) — brew install kubectl\n"
	@command -v kind     >/dev/null 2>&1 && printf "  $(GREEN)✓$(RESET) kind\n"      || printf "  $(RED)✗ kind$(RESET) — brew install kind\n"
	@command -v uv       >/dev/null 2>&1 && printf "  $(GREEN)✓$(RESET) uv\n"        || printf "  $(RED)✗ uv$(RESET) — brew install uv\n"
	@command -v node     >/dev/null 2>&1 && printf "  $(GREEN)✓$(RESET) node\n"      || printf "  $(RED)✗ node$(RESET) — brew install node\n"
	@command -v npm      >/dev/null 2>&1 && printf "  $(GREEN)✓$(RESET) npm\n"       || printf "  $(RED)✗ npm$(RESET) — install with node\n"
	@lsof -i :8000 >/dev/null 2>&1 && printf "  $(RED)✗ port 8000 in use$(RESET)\n" || printf "  $(GREEN)✓$(RESET) port 8000 free\n"
	@lsof -i :3000 >/dev/null 2>&1 && printf "  $(RED)✗ port 3000 in use$(RESET)\n" || printf "  $(GREEN)✓$(RESET) port 3000 free\n"
	@printf "\nRun $(BOLD)make bootstrap$(RESET) to install dependencies.\n"

# ── Bootstrap ────────────────────────────────────────────────────────────────
.PHONY: bootstrap
bootstrap: install-python install-node kind-create kind-rbac ## Install all deps, create kind cluster, apply RBAC
	@printf "$(GREEN)$(BOLD)Bootstrap complete.$(RESET) Run: make dev-api  /  make dev-web\n"

.PHONY: install-python
install-python: ## Install Python dependencies via uv
	@printf "$(BOLD)Installing Python dependencies…$(RESET)\n"
	uv sync --dev
	@printf "$(GREEN)✓$(RESET) Python deps installed\n"

.PHONY: install-node
install-node: ## Install Node.js dependencies
	@printf "$(BOLD)Installing Node dependencies…$(RESET)\n"
	cd $(WEB_DIR) && npm install --legacy-peer-deps --silent
	@printf "$(GREEN)✓$(RESET) Node deps installed\n"

.PHONY: kind-create
kind-create: ## Create the incidentforge kind cluster (idempotent)
	@if kind get clusters 2>/dev/null | grep -q '^incidentforge$$'; then \
		printf "  $(GREEN)✓$(RESET) kind cluster 'incidentforge' already exists\n"; \
	else \
		printf "$(BOLD)Creating kind cluster 'incidentforge'…$(RESET)\n"; \
		kind create cluster --name incidentforge --config infra/kind/cluster.yaml; \
		printf "$(GREEN)✓$(RESET) kind cluster created\n"; \
	fi

.PHONY: kind-rbac
kind-rbac: ## Apply RBAC roles to the kind cluster
	@printf "$(BOLD)Applying RBAC…$(RESET)\n"
	kubectl --context kind-incidentforge apply -f infra/kind/rbac.yaml
	@printf "$(GREEN)✓$(RESET) RBAC applied\n"

# ── Development servers ──────────────────────────────────────────────────────
.PHONY: dev-api
dev-api: ## Start the FastAPI dev server on :8000 (auto-reload)
	@printf "$(BOLD)Starting API server on http://127.0.0.1:8000$(RESET)\n"
	cd $(API_DIR) && uv run --directory $(ROOT) uvicorn main:app \
		--host 127.0.0.1 --port 8000 --reload \
		--reload-dir $(ROOT)/contracts \
		--reload-dir $(API_DIR)

.PHONY: dev-web
dev-web: ## Start the Next.js dev server on :3000
	@printf "$(BOLD)Starting web server on http://localhost:3000$(RESET)\n"
	cd $(WEB_DIR) && npm run dev

# ── Testing ──────────────────────────────────────────────────────────────────
.PHONY: test
test: ## Run unit + contract tests (no live infra required)
	@printf "$(BOLD)Running unit and contract tests…$(RESET)\n"
	uv run pytest tests/unit/ -v --tb=short

.PHONY: test-integration
test-integration: ## Run integration tests (requires kind cluster)
	@printf "$(BOLD)Running integration tests…$(RESET)\n"
	uv run pytest tests/integration/ -v --tb=short

.PHONY: test-e2e
test-e2e: ## Run end-to-end tests (requires both servers running)
	@printf "$(BOLD)Running E2E tests…$(RESET)\n"
	uv run pytest tests/e2e/ -v --tb=short

.PHONY: test-all
test-all: test test-integration ## Run all tests

# ── Code quality ─────────────────────────────────────────────────────────────
.PHONY: lint
lint: ## Run ruff (Python) linter
	uv run ruff check contracts/ services/ tests/

.PHONY: format
format: ## Auto-format Python code with ruff
	uv run ruff format contracts/ services/ tests/
	uv run ruff check --fix contracts/ services/ tests/

.PHONY: typecheck
typecheck: ## Run mypy type checks
	uv run mypy contracts/ services/api/app/ --ignore-missing-imports

# ── Demo ─────────────────────────────────────────────────────────────────────
.PHONY: demo
demo: ## Reset lab and run the k8s-readiness-path scenario end to end
	@printf "$(BOLD)IncidentForge Demo$(RESET)\n"
	@printf "Make sure dev-api is running: $(CYAN)make dev-api$(RESET)\n\n"
	bash scripts/demo.sh k8s-readiness-path openai gpt-4o

# ── AWS Emulator ─────────────────────────────────────────────────────────────
.PHONY: emulator-build
emulator-build: ## Build the AWS Emulator Docker image (Apple Silicon / aarch64)
	@printf "$(BOLD)Building AWS Emulator image…$(RESET)\n"
	@printf "$(RED)TODO (IF-010):$(RESET) Complete infra/aws-emulator/Dockerfile per official AWS docs\n"
	@printf "See docs/architecture-review.md §2.8 — AWS Emulator is P1\n"

.PHONY: emulator-up
emulator-up: ## Start the AWS Emulator on 127.0.0.1:9297
	@printf "$(RED)TODO (IF-010):$(RESET) emulator-up not yet implemented\n"

.PHONY: emulator-smoke
emulator-smoke: ## Verify emulator HTTPS + required S3 operations
	@printf "$(RED)TODO (IF-010):$(RESET) emulator-smoke not yet implemented\n"

.PHONY: emulator-down
emulator-down: ## Stop the AWS Emulator container
	@printf "$(RED)TODO (IF-010):$(RESET) emulator-down not yet implemented\n"

# ── Cleanup ───────────────────────────────────────────────────────────────────
.PHONY: clean
clean: ## Remove scenario-owned k8s namespaces and local data files
	@printf "$(BOLD)Cleaning scenario resources…$(RESET)\n"
	@kubectl --context kind-incidentforge get namespaces \
		-l app.kubernetes.io/part-of=incidentforge \
		-o name 2>/dev/null | xargs -r kubectl --context kind-incidentforge delete || true
	@rm -rf data/*.db data/*.db-wal data/*.db-shm
	@printf "$(GREEN)✓$(RESET) Clean complete\n"

.PHONY: clean-cluster
clean-cluster: ## Delete the kind cluster entirely
	@printf "$(BOLD)Deleting kind cluster 'incidentforge'…$(RESET)\n"
	kind delete cluster --name incidentforge
	@printf "$(GREEN)✓$(RESET) Cluster deleted\n"
