.PHONY: install fmt lint typecheck test check run diagrams metrics-history metrics-diff

install:
	uv sync

fmt:
	uv run ruff format src/ tests/

lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/

typecheck:
	uv run pyright src/ tests/

test:
	uv run pytest tests/ -q

# Serve the dashboard against the registry in your XDG config
# ($XDG_CONFIG_HOME/guardrail-hub/config.toml, or ./config.toml, or
# GUARDRAIL_HUB_CONFIG).
run:
	uv run guardrail-hub serve

# Regenerate docs/generated/ (architecture + domain diagrams, metrics, index,
# per-component pages). Note `make test` runs the same tests, so a test run
# rewrites docs/generated/ as a side effect — commit the result if it changed.
diagrams:
	uv run pytest tests/guardrail/ -q

# Print the architecture-metrics trend mined from the git history of
# docs/generated/metrics.json. FORMAT=csv|mermaid (default csv).
metrics-history:
	uv run python scripts/metrics_history.py --format $(or $(FORMAT),csv)

# Show the metrics delta between BASE (default origin/main) and the working tree.
# `set -e` + `trap` so the recipe exits with metrics_diff.py's status, not rm's.
metrics-diff:
	@set -e; tmp=$$(mktemp); trap 'rm -f $$tmp' EXIT; \
	git show $(or $(BASE),origin/main):docs/generated/metrics.json > $$tmp 2>/dev/null || echo '{}' > $$tmp; \
	uv run python scripts/metrics_diff.py $$tmp docs/generated/metrics.json

check: lint typecheck test
