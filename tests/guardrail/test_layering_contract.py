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

import pytest
from tests.guardrail import _diagram_toolkit as dt
from tests.guardrail._common import LANGUAGE, load_architecture


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
