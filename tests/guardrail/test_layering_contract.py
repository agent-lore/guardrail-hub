"""Enforce the directional architecture contract.

Python projects: the contracts live in ``pyproject.toml`` under
``[tool.importlinter]`` (dependencies must only point downward,
Entrypoints -> Core -> Foundation, expressed as ``forbidden`` contracts); this
test runs ``lint-imports`` and fails with its report if any contract is broken.

C++ projects (``[project] language = "cpp"``): the same downward-only rule is
asserted directly on the include graph — no component may depend on one in a
higher tier (tier order = declaration order in ``[tiers]``).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib

import pytest
from tests.guardrail import _diagram_toolkit as dt
from tests.guardrail._common import LANGUAGE, REPO_ROOT, load_architecture


def _assert_import_linter_contracts() -> None:
    exe = shutil.which("lint-imports")
    cmd = [exe] if exe else [sys.executable, "-m", "importlinter"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:  # pragma: no cover - import-linter must be installed
        pytest.skip("import-linter not installed")
    assert result.returncode == 0, (
        "import-linter architecture contracts broken:\n" + result.stdout + result.stderr
    )


def _assert_no_upward_tier_edges() -> None:
    arch = load_architecture()
    tiers: dict[str, list[str]] = arch.get("tiers", {})
    assert tiers, "docs/architecture.toml needs [tiers] to enforce the layering contract"
    rank = {comp: i for i, members in enumerate(tiers.values()) for comp in members}
    upward = sorted(
        f"{src} -> {dst}"
        for src, dst in dt.component_edges(arch["components"])
        if src in rank and dst in rank and rank[dst] < rank[src]
    )
    assert not upward, (
        "dependencies must only point downward through [tiers]; upward edges found:\n"
        + "\n".join(f"  {edge}" for edge in upward)
    )


def test_layering_contract_holds() -> None:
    if LANGUAGE == "cpp":
        _assert_no_upward_tier_edges()
    else:
        _assert_import_linter_contracts()


def _tier_prefixes(arch: dict) -> list[set[str]]:
    """Module-prefix sets per declared tier, in [tiers] declaration order."""
    components: dict[str, list[str]] = arch["components"]
    return [
        {prefix for comp in members if comp in components for prefix in components[comp]}
        for members in arch.get("tiers", {}).values()
    ]


def test_importlinter_contracts_match_architecture_toml() -> None:
    """The manual [tool.importlinter] lists must mirror docs/architecture.toml.

    [tiers]/[components] drive the diagram and metrics; import-linter enforces
    the same direction from hand-maintained module lists. A component added,
    moved, or renamed in only one of the two places would silently weaken
    enforcement — this test pins them together, assuming the kit's standard
    two-contract convention (Foundation must not import upward; Core must not
    import Entrypoints).
    """
    if LANGUAGE != "python":
        pytest.skip("cpp derives the contract from [tiers] directly — nothing to synchronize")
    arch = load_architecture()
    tiers = _tier_prefixes(arch)
    assert len(tiers) == 3, "the two-contract convention assumes three [tiers]"

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    contracts = [
        c
        for c in pyproject.get("tool", {}).get("importlinter", {}).get("contracts", [])
        if c.get("type") == "forbidden"
    ]
    assert len(contracts) == 2, (
        "expected exactly the two forbidden contracts (Foundation / Core) in "
        "pyproject.toml [tool.importlinter]"
    )
    # The Foundation contract forbids both higher tiers, so it always has the
    # larger forbidden set — identify the pair by shape, not by name.
    core_c, foundation_c = sorted(contracts, key=lambda c: len(c.get("forbidden_modules", [])))

    entrypoints, core, foundation = tiers
    expected = {
        "Foundation contract source_modules": (
            set(foundation_c.get("source_modules", [])),
            foundation,
        ),
        "Foundation contract forbidden_modules": (
            set(foundation_c.get("forbidden_modules", [])),
            entrypoints | core,
        ),
        "Core contract source_modules": (set(core_c.get("source_modules", [])), core),
        "Core contract forbidden_modules": (set(core_c.get("forbidden_modules", [])), entrypoints),
    }
    mismatches = [
        f"{what}: only in pyproject {sorted(got - want)}, only in architecture.toml "
        f"{sorted(want - got)}"
        for what, (got, want) in expected.items()
        if got != want
    ]
    assert not mismatches, (
        "pyproject.toml [tool.importlinter] disagrees with docs/architecture.toml "
        "[tiers]/[components] — update whichever side is stale:\n  " + "\n  ".join(mismatches)
    )
