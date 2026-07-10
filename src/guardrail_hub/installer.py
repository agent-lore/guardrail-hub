"""Kit installer: port the canonical kit into a target repo.

``apply_kit`` writes the kit files plus a prefilled ``docs/architecture.toml``
skeleton, then returns a printed report of Makefile/CI/pyproject snippets and
the port checklist. It NEVER edits existing files: if any destination already
exists it aborts listing every conflict, and the surrounding wiring (Makefile
targets, CI job, dev deps) is printed for a human to paste, not patched in.
"""

from __future__ import annotations

import shutil
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from guardrail_hub.errors import KitError
from guardrail_hub.kit import KitFile, kit_root, kit_version, load_manifest

VERSION_FILE = "tests/guardrail/KIT_VERSION"
ARCHITECTURE_TOML = "docs/architecture.toml"

# ── target inspection ──────────────────────────────────────────────────


def _load_pyproject(target: Path) -> dict[str, Any]:
    path = target / "pyproject.toml"
    if not path.is_file():
        raise KitError(f"{target} has no pyproject.toml — not a Python project?")
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise KitError(f"invalid pyproject.toml in {target}: {exc}") from exc


def _detect_root_package(pyproject: Mapping[str, Any], override: str | None) -> str:
    if override:
        return override
    linter_pkg = pyproject.get("tool", {}).get("importlinter", {}).get("root_package")
    if isinstance(linter_pkg, str) and linter_pkg:
        return linter_pkg
    name = pyproject.get("project", {}).get("name")
    if isinstance(name, str) and name:
        return name.replace("-", "_")
    raise KitError("cannot detect the root package; pass --root-package")


def _detect_src_layout(target: Path, root_package: str) -> str:
    if (target / "src" / root_package).is_dir():
        return "src"
    if (target / root_package).is_dir():
        return ""
    raise KitError(
        f"package {root_package!r} found neither at src/{root_package} nor ./{root_package}; "
        "pass --root-package if detection picked the wrong name"
    )


# ── architecture.toml prefill ──────────────────────────────────────────


def _component_name(module: str) -> str:
    stem = module.rsplit(".", 1)[-1]
    return "".join(part.capitalize() for part in stem.split("_")) or stem


def _tiers_from_importlinter(
    pyproject: Mapping[str, Any], root_package: str
) -> dict[str, list[str]] | None:
    """Infer Foundation/Core/Entrypoints module lists from the two-contract convention.

    The contract with the larger forbidden set names Foundation (its sources);
    modules only ever forbidden are Entrypoints; the other contract's sources
    minus Foundation are Core. Returns None when the convention doesn't apply.
    """
    contracts = pyproject.get("tool", {}).get("importlinter", {}).get("contracts")
    if not isinstance(contracts, list) or len(contracts) < 2:
        return None
    forbidden_type = [c for c in contracts if c.get("type") == "forbidden"]
    if len(forbidden_type) < 2:
        return None
    ranked = sorted(forbidden_type, key=lambda c: len(c.get("forbidden_modules", [])))
    foundation_contract, core_contract = ranked[-1], ranked[-2]

    def _mods(contract: Mapping[str, Any], key: str) -> list[str]:
        return [
            m
            for m in contract.get(key, [])
            if isinstance(m, str) and (m == root_package or m.startswith(root_package + "."))
        ]

    foundation = _mods(foundation_contract, "source_modules")
    all_sources = {m for c in forbidden_type for m in _mods(c, "source_modules")}
    all_forbidden = {m for c in forbidden_type for m in _mods(c, "forbidden_modules")}
    entrypoints = sorted(all_forbidden - all_sources)
    core = [m for m in _mods(core_contract, "source_modules") if m not in set(foundation)]
    if not foundation or not entrypoints:
        return None
    return {"Entrypoints": entrypoints, "Core": core, "Foundation": foundation}


def _padded(pairs: list[tuple[str, str]]) -> list[str]:
    width = max((len(k) for k, _ in pairs), default=0)
    return [f"{k.ljust(width)} = {v}" for k, v in pairs]


def build_architecture_skeleton(
    pyproject: Mapping[str, Any], root_package: str, src_layout: str
) -> str:
    """The prefilled docs/architecture.toml for a new port."""
    template = (kit_root() / "docs" / "architecture.skeleton.toml").read_text(encoding="utf-8")
    tiers = _tiers_from_importlinter(pyproject, root_package)

    if tiers is None:
        component_docs = ['# TODO = "One line per component, e.g.:"', '# Core = "The heart."']
        components = [
            "# TODO: map every module to a component, e.g.:",
            f'# Core = ["{root_package}.core"]',
        ]
        tier_lines = [
            "# TODO: group components into tiers, e.g.:",
            '# Entrypoints = ["Cli"]',
            '# Core = ["Core"]',
            "# Foundation = []",
        ]
    else:
        modules = [m for tier_modules in tiers.values() for m in tier_modules]
        names = {m: _component_name(m) for m in modules}
        component_docs = _padded(
            [(names[m], '"TODO: one-line description."') for m in sorted(modules)]
        )
        components = _padded([(names[m], f'["{m}"]') for m in sorted(modules)])
        tier_lines = _padded(
            [
                (tier, "[" + ", ".join(f'"{names[m]}"' for m in tiers[tier]) + "]")
                for tier in ("Entrypoints", "Core", "Foundation")
            ]
        )
        components.append("# TODO: merge single-module components into real ones where it helps.")

    budgets = [
        "# cross_component_edges  = TODO  # measured <date>; direction: down",
        "# component_cycles       = TODO",
        "# module_cycles          = TODO",
        "# modules_over_800_lines = TODO",
        "# max_module_lines       = TODO  # stop-loss, a little above today's largest module",
    ]
    return template.format(
        kit_version=kit_version(),
        root_package=root_package,
        src_layout=src_layout,
        component_docs_block="\n".join(component_docs),
        components_block="\n".join(components),
        tiers_block="\n".join(tier_lines),
        budgets_block="\n".join(budgets),
    )


# ── snippets & checklist (printed, never written) ──────────────────────

_MAKEFILE_SNIPPET = """\
# Regenerate docs/generated/ (architecture + domain diagrams, metrics, index,
# per-component pages). Note `make test` runs the same tests, so a test run
# rewrites docs/generated/ as a side effect — commit the result if it changed.
diagrams:
\tuv run pytest tests/guardrail/ -q

# Print the architecture-metrics trend mined from the git history of
# docs/generated/metrics.json. FORMAT=csv|mermaid (default csv).
metrics-history:
\tuv run python scripts/metrics_history.py --format $(or $(FORMAT),csv)

# Show the metrics delta between BASE (default origin/main) and the working tree.
metrics-diff:
\t@set -e; tmp=$$(mktemp); trap 'rm -f $$tmp' EXIT; \\
\tgit show $(or $(BASE),origin/main):docs/generated/metrics.json > $$tmp 2>/dev/null || echo '{}' > $$tmp; \\
\tuv run python scripts/metrics_diff.py $$tmp docs/generated/metrics.json
"""

_CI_SNIPPET = """\
  diagrams:
    name: Diagram drift
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
        with:
          version: "latest"
      - run: uv sync --locked
      - name: Regenerate architecture & domain diagrams
        run: make diagrams
      - name: Fail if diagrams are stale
        # `git status --porcelain` (not `git diff`) so newly generated but
        # never-committed artifacts fail the gate too, not just modifications.
        run: |
          if [ -n "$(git status --porcelain docs/generated)" ]; then
            echo "::error::docs/generated is out of date. Run 'make diagrams' and commit the result."
            git status --short docs/generated
            git --no-pager diff -- docs/generated
            exit 1
          fi
      - name: Architecture metrics delta vs base (informational)
        if: github.event_name == 'pull_request'
        run: |
          git fetch --no-tags --depth=1 origin "${{ github.base_ref }}"
          git show "FETCH_HEAD:docs/generated/metrics.json" > /tmp/base-metrics.json 2>/dev/null || echo '{}' > /tmp/base-metrics.json
          uv run python scripts/metrics_diff.py /tmp/base-metrics.json docs/generated/metrics.json --markdown >> "$GITHUB_STEP_SUMMARY"
"""

_CHECKLIST = """\
1. Add the dev dependencies the generators need (uv add --group dev):
     grimp>=3.14  import-linter>=2.12  networkx>=3.4
2. Ensure pytest can import the kit as a package — add to [tool.pytest.ini_options]:
     pythonpath = ["."]
   (or keep an existing tests/__init__.py chain if the repo already has one).
3. If pyproject has no [tool.importlinter] contracts yet, add the two forbidden
   contracts (Foundation must not import Core/Entrypoints; Core must not import
   Entrypoints) — tests/guardrail/test_layering_contract.py enforces them.
4. Review docs/architecture.toml: resolve every TODO — merge single-module
   components into meaningful ones, write [component_docs] one-liners, classify
   [domain] include/exclude until the completeness test passes.
5. Run `make diagrams` TWICE. First-run gotcha: with an empty docs/generated/,
   the index/manifest tests run before the generators (alphabetical order), so
   the very first run can fail on missing artifacts; the second run passes.
6. Read the measured values from docs/generated/metrics.md and pin [budgets]
   (uncomment, fill numbers; max_module_lines a little above today's largest).
7. Run `make diagrams` again after the budget edit and commit docs/generated/.
8. Add the Makefile targets and the CI 'Diagram drift' job (snippets above);
   update .PHONY.
9. `make check` green, commit, PR.
10. Register the repo in your guardrail-hub config
    (~/.config/guardrail-hub/config.toml) so the dashboard picks it up.
"""


def _files_to_apply(with_tool_catalog: bool, with_containers: bool) -> list[KitFile]:
    wanted_roles = {"core", "doc"}
    if with_tool_catalog:
        wanted_roles.add("adapter-tool-catalog")
    if with_containers:
        wanted_roles.add("adapter-containers")
    return [f for f in load_manifest() if f.role in wanted_roles]


def apply_kit(
    target: Path,
    *,
    root_package: str | None = None,
    with_tool_catalog: bool = False,
    with_containers: bool = False,
) -> str:
    """Port the kit into ``target``; returns the human report. Never edits files."""
    if not target.is_dir():
        raise KitError(f"target {target} is not a directory")
    if not (target / ".git").exists():
        raise KitError(f"target {target} is not a git repository")
    pyproject = _load_pyproject(target)
    package = _detect_root_package(pyproject, root_package)
    src_layout = _detect_src_layout(target, package)

    kit_files = _files_to_apply(with_tool_catalog, with_containers)
    destinations = [f.path for f in kit_files] + [ARCHITECTURE_TOML, VERSION_FILE]
    conflicts = sorted(d for d in destinations if (target / d).exists())
    if conflicts:
        raise KitError(
            "refusing to overwrite existing files (the installer never edits):\n  "
            + "\n  ".join(conflicts)
        )

    skeleton = build_architecture_skeleton(pyproject, package, src_layout)
    for kit_file in kit_files:
        dest = target / kit_file.path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(kit_root() / kit_file.path, dest)
    (target / ARCHITECTURE_TOML).parent.mkdir(parents=True, exist_ok=True)
    (target / ARCHITECTURE_TOML).write_text(skeleton, encoding="utf-8")
    (target / VERSION_FILE).write_text(kit_version() + "\n", encoding="utf-8")

    written = sorted(destinations)
    adapters = [
        name
        for enabled, name in ((with_tool_catalog, "tool-catalog"), (with_containers, "containers"))
        if enabled
    ]
    return (
        f"Applied guardrail kit {kit_version()} to {target}\n"
        f"root_package = {package!r}, src_layout = {src_layout!r}, "
        f"adapters = {adapters or 'none'}\n\n"
        "Files written:\n" + "\n".join(f"  {p}" for p in written) + "\n\n"
        "── Makefile targets to add "
        "──────────────────────────────────────────\n" + _MAKEFILE_SNIPPET + "\n"
        "── CI job to add (.github/workflows/ci.yml) "
        "─────────────────────────\n" + _CI_SNIPPET + "\n"
        "── Port checklist "
        "───────────────────────────────────────────────────\n" + _CHECKLIST
    )
