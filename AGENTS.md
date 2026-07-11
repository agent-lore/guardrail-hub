# guardrail-hub — Agent Guide

guardrail-hub is the canonical home of the "diagrams as tests" architecture
guardrail kit, plus a local FastAPI dashboard that traces metrics, budgets, and
diagrams across every repo the kit is installed in (lithos, lithos-lens,
lithos-loom, influx, kc-agent, kc-sim, the two robot-companion monorepo
instances — and this repo itself).

Two jobs, two surfaces:

1. **Canonical kit + installer** — `kit/` is the one true copy of the toolkit.
   `guardrail-hub apply <repo>` ports it (prefilling `docs/architecture.toml`
   from the target's import-linter contracts); `--language cpp` ports to a C++
   project instead (quoted-`#include` graph, no compiler needed; Python-only
   views omitted/zeroed). `guardrail-hub drift` reports every registered
   repo's divergence from the canon (AST-normalized, so formatting/docstring
   differences don't count). Monorepos register one entry per kit instance
   via the config's `subdir` field.
2. **Dashboard** — `guardrail-hub serve` renders repo cards, budget
   traffic-lights, metric trends (mined from each repo's committed
   `metrics.json` git history), Mermaid diagrams, and the drift panel.

## Done Criteria (all must be green)

```bash
make lint        # ruff check + format check (src/, tests/)
make typecheck   # pyright standard mode (src/ + tests/)
make test        # unit tests (fixture git repos, never the real checkouts)
make check       # lint + typecheck + test — the definition of done
```

## Tooling

Identical to the sibling repos: uv (PEP 735 dev group, committed `uv.lock`),
hatchling, ruff (line 100, E/F/I/UP/B/SIM/RUF, E501 off, double quotes),
pyright standard, pytest (`pythonpath = ["."]`, strict config/markers),
import-linter (Foundation → Core → Entrypoints), Makefile targets
`install/fmt/lint/typecheck/test/check/run/diagrams/metrics-history/metrics-diff`.
Python 3.12 only (pinned `<3.13` — the drift engine compares `ast.dump` output,
which must come from one interpreter generation).

## Configuration

The registry of monitored checkouts lives OUTSIDE this repo:
`$XDG_CONFIG_HOME/guardrail-hub/config.toml` (usually
`~/.config/guardrail-hub/`), overridable by `./config.toml` in the CWD
(gitignored) or `GUARDRAIL_HUB_CONFIG`. `config.example.toml` is the template.

## Layout

Strict src layout, flat modules in three layers (import-linter enforced):

- **Foundation**: `errors`, `models` (frozen value types), `config` (XDG
  discovery + env overrides), `logging_setup`
- **Core**: `gitio` (the ONE git subprocess seam), `repo_scan` (worktree →
  RepoSnapshot), `budgets`, `history` (first-parent metrics.json mining),
  `store` (cache keyed on HEAD sha + mtimes), `docs_render` (markdown-it +
  mermaid fences + escape-link rewriting), `drift` (normalized AST compare),
  `kit` (canon access), `installer`
- **Entrypoints**: `web` (FastAPI app factory), `cli`, `__main__`

`kit/` is NOT hub source code — it is the canonical kit payload, excluded from
pytest/ruff/pyright and shipped in wheels as `guardrail_hub/_kit`. The hub
applies its own kit (dogfood): `tests/guardrail/` must stay **byte-identical**
to `kit/` (`tests/test_kit_dogfood.py`) — edit the canon in `kit/`, re-copy,
never patch the applied files.

## Rules for changes

1. **Kit changes happen in `kit/`**, get a `kit/VERSION` bump, must keep
   `kit/manifest.toml` in closure (`tests/test_kit_manifest.py`), and are
   re-applied to `tests/guardrail/` so the dogfood byte-identity test passes.
2. **All git access goes through `gitio`** — no `subprocess` elsewhere.
3. **The web layer never reads repos directly** — everything via `RepoStore`.
4. **Repo-side problems are badges, not exceptions**: a missing/dirty/broken
   checkout must render, never 500. `RepoStatus.error` carries the story.
5. **Every file-serving route is containment-checked**
   (`docs_render.contained`) — links inside repo markdown are data, not trust.
6. **Tests never touch the real registered repos**; fixtures build tiny git
   repos in tmp dirs (`tests/conftest.py::make_repo`).
7. Timestamps/randomness stay out of generated artifacts (the kit's
   determinism contract applies to the hub's own docs/generated/ too).
8. **NEVER use implicit string concatenation** when splitting a long string
   (in kit code or when reflowing kit files for an 88-width port) — GitHub
   code quality flags it (especially inside lists, where it looks like a
   missing comma) and has complained on TWO adoption waves now. Use explicit
   `+` between the pieces; a placeholder-less piece must be a plain string,
   not an `f`-string (ruff F541). The drift engine's `_FoldStringConcat`
   normalizes `+`-joined plain AND f-string pieces back to the canonical
   literal, so explicit `+` never shows as drift.

## Architecture guardrails & generated docs

`docs/generated/` holds generated views of this repo's code — produced by
`tests/guardrail/` (the dogfooded kit) and drift-checked in CI:

- `make diagrams` regenerates everything; `make test` runs the same tests, so a
  test run rewrites `docs/generated/` as a side effect — commit if changed.
- The CI job `Diagram drift` fails when the committed files disagree with the
  code. Fix: `make diagrams`, commit.
- `docs/architecture.toml` is the source of truth for components, tiers,
  domain scanning, and the hard metric budgets.
- `tests/guardrail/AGENTS.md` has the generator contracts.
