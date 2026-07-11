# guardrail-hub

Canonical home of the "diagrams as tests" architecture guardrail kit, plus a
local web dashboard that traces architecture metrics, budgets, and diagrams
across every repo the kit is installed in.

Two jobs:

1. **Canonical kit + installer** — `kit/` holds the one true copy of the
   guardrail toolkit (`tests/guardrail/`, `scripts/metrics_*.py`).
   `guardrail-hub apply <repo>` ports it to a new project, prefilling
   `docs/architecture.toml` from the target's import-linter contracts.
   `--language cpp` ports it to a C++ project instead: the same generators run
   over the quoted-`#include` graph (no compiler needed), with Python-only
   views (domain model, complexity) omitted or zeroed.
2. **Dashboard** — `guardrail-hub serve` reads your locally checked-out repos
   (registered in an XDG config) and serves metric trends, cross-repo
   comparisons, budget traffic-lights, rendered diagrams, and a kit-drift panel.
   Monorepos register one entry per kit instance via `subdir` (see
   `config.example.toml`).

## Installation

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

## Configuration

The repo registry lives outside this repo. Copy `config.example.toml` to
`~/.config/guardrail-hub/config.toml` (or `$XDG_CONFIG_HOME/guardrail-hub/`)
and adjust the paths. A `./config.toml` in the CWD overrides it for
development; `GUARDRAIL_HUB_CONFIG=<path>` overrides everything.

## Quick start

```bash
make run                      # serve the dashboard on http://127.0.0.1:8600
uv run guardrail-hub drift    # kit drift report in the terminal
uv run guardrail-hub apply ~/src/new-project   # port the kit to a new repo
uv run guardrail-hub apply ~/src/robot/client --language cpp --root-package agent
```

## Development

```bash
make install     # uv sync
make fmt         # auto-format with ruff
make lint        # ruff check + format check
make typecheck   # pyright (standard mode)
make test        # unit tests
make check       # lint + typecheck + test — the definition of done
make diagrams    # regenerate docs/generated/ (the hub dogfoods its own kit)
```

See `AGENTS.md` for the full development guide.
