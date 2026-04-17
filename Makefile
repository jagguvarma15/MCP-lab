.PHONY: install test test-security test-conformance test-transport test-integration test-evaluation test-fuzz test-fast profile tools clean help

# ── Installation ──────────────────────────────────────────────────────

install: ## Install all dependencies
	uv sync --all-groups

# ── Testing ───────────────────────────────────────────────────────────

test: ## Run the full test suite
	uv run mcplab test

test-security: ## Run security tests only
	uv run mcplab test security

test-conformance: ## Run conformance tests only
	uv run mcplab test conformance

test-transport: ## Run transport tests only
	uv run mcplab test transport

test-integration: ## Run integration tests only
	uv run mcplab test integration

test-evaluation: ## Run evaluation tests only
	uv run mcplab test evaluation

test-fuzz: ## Run fuzz tests only
	uv run mcplab test fuzz

test-fast: ## Run all tests except slow ones
	uv run mcplab test fast

# ── Clean ─────────────────────────────────────────────────────────────

clean: ## Remove build artifacts and caches
	rm -rf .venv .pytest_cache .hypothesis __pycache__ harness/__pycache__ tests/__pycache__
	rm -rf dist build *.egg-info mcp_lab.egg-info
	rm -f benchmark_results.json profile_results.json traffic.jsonl
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# ── Help ──────────────────────────────────────────────────────────────

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
