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
   comparisons, budget traffic-lights, rendered diagrams, a kit-drift panel,
   per-repo hotspots (mainline churn x complexity), and a fleet-wide budget
   ledger (every ratchet raise/lower, mined from architecture.toml history).
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

For startup at boot and operation after logout, see
[Run as a systemd user service](docs/user-service.md). The guide includes example
units, a dedicated installation, optional GitHub authentication, and explicit
upgrade and rollback steps.

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

## Quality review

Kit 1.6 records measurement provenance and all function identities. The dashboard
preserves individual commits, distinguishes missing/unsupported/stale data, labels
hotspot calculation methods, and compares two revisions with dependency, domain,
function, policy, and budget changes. Historical source links open the selected
revision.

Policies distinguish invariants from review thresholds. Bounded exceptions record
why cohesive code should remain together; optional changed-code gates require a
comparable baseline. Coverage, mutation results, and intervention outcomes can be
recorded with explicit scope and evidence. Test/source ratio is only a volume
statistic. See [the quality guide](docs/quality-guide.md) for the configuration,
evidence format, and upgrade sequence. Generated artifacts remain in Git.

The hub runs a [changed-code enforcement pilot](docs/enforcement-pilot.md): new
and worsened entity findings block PRs unless a bounded exception accepts them.
Existing findings remain visible. The pilot documents the baseline decisions,
local check command, and criteria for evaluating usefulness before fleet rollout.

Development checks require Python 3.12 and Node.js 22 (chart regression tests).
