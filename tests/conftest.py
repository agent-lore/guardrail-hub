"""Shared fixtures: tiny real git repos with synthetic kit artifacts.

Every data-layer and web test runs against repos built here — never against the
user's real checkouts.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from guardrail_hub.models import RepoEntry

_METRICS_TEMPLATE: dict[str, Any] = {
    "schema": 1,
    "graph": {
        "components": {
            "Core": {"fan_in": 1, "fan_out": 1, "instability": 0.5},
            "Web": {"fan_in": 0, "fan_out": 2, "instability": 1.0},
        },
        "component_cycles": [],
        "cross_component_edges": 5,
        "module_cycle_count": 0,
        "module_cycles": [],
        "tier_skipping": [],
        "tier_skipping_edges": 0,
        "longest_component_chain": 2,
    },
    "size": {
        "components": {
            "Core": {"modules": 2, "lines": 300, "sloc": 200},
            "Web": {"modules": 1, "lines": 150, "sloc": 100},
        },
        "total_sloc": 300,
        "total_lines": 450,
        "total_modules": 3,
        "max_module_lines": 200,
        "max_module": "core.py",
        "modules_over_800": [],
    },
    "complexity": {
        "components": {
            "Core": {"max_complexity": 6, "functions_over_10": 0},
            "Web": {"max_complexity": 3, "functions_over_10": 0},
        },
        "total_functions": 20,
        "functions_over_10": 0,
        "top_functions": [],
    },
    "domain": {"models": 4, "associations": 2, "models_without_docstrings": 0},
    "tests": {"src_lines": 300, "test_lines": 300, "ratio": 1.0},
}

_ARCHITECTURE_TOML = """
[project]
root_package = "fixture"
src_layout = "src"

[components]
Core = ["fixture.core"]
Web = ["fixture.web"]

[tiers]
Entrypoints = ["Web"]
Core = ["Core"]

[budgets]
cross_component_edges = 5
component_cycles = 0
modules_over_800_lines = 0
max_module_lines = 800
"""


def _run(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def make_metrics(**overrides: Any) -> dict[str, Any]:
    """Deep-copy the template metrics with top-level section overrides."""
    metrics = json.loads(json.dumps(_METRICS_TEMPLATE))
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(metrics.get(key), dict):
            metrics[key].update(value)
        else:
            metrics[key] = value
    return metrics


def commit_snapshot(
    repo: Path, metrics: dict[str, Any] | str, message: str = "update", subdir: str = ""
) -> str:
    """Write metrics.json (dict, or a raw string for malformed cases) and commit."""
    generated = (repo / subdir if subdir else repo) / "docs" / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    content = metrics if isinstance(metrics, str) else json.dumps(metrics, indent=2)
    (generated / "metrics.json").write_text(content, encoding="utf-8")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-q", "-m", message, "--no-verify")
    return _run(repo, "rev-parse", "--short", "HEAD").strip()


def make_repo(root: Path, snapshots: list[dict[str, Any] | str] | None = None) -> Path:
    """A real git repo with architecture.toml and a committed snapshot sequence."""
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    _run(repo, "init", "-q", "-b", "main")
    _run(repo, "config", "user.email", "test@example.com")
    _run(repo, "config", "user.name", "Test")
    docs = repo / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "architecture.toml").write_text(_ARCHITECTURE_TOML, encoding="utf-8")
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    for i, snapshot in enumerate(snapshots if snapshots is not None else [make_metrics()]):
        commit_snapshot(repo, snapshot, message=f"snapshot {i}")
    return repo


def make_monorepo(
    root: Path, subdir: str = "server", snapshots: list[dict[str, Any] | str] | None = None
) -> Path:
    """A git repo whose kit instance lives under a subdirectory (monorepo shape)."""
    repo = root / "mono"
    repo.mkdir(parents=True, exist_ok=True)
    _run(repo, "init", "-q", "-b", "main")
    _run(repo, "config", "user.email", "test@example.com")
    _run(repo, "config", "user.name", "Test")
    docs = repo / subdir / "docs"
    docs.mkdir(parents=True)
    (docs / "architecture.toml").write_text(_ARCHITECTURE_TOML, encoding="utf-8")
    (repo / "README.md").write_text("monorepo fixture\n", encoding="utf-8")
    for i, snapshot in enumerate(snapshots if snapshots is not None else [make_metrics()]):
        commit_snapshot(repo, snapshot, message=f"snapshot {i}", subdir=subdir)
    return repo


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path)


@pytest.fixture
def fixture_entry(fixture_repo: Path) -> RepoEntry:
    return RepoEntry(name="fixture", path=fixture_repo, family="test")


def git(repo: Path, *args: str) -> str:
    """Test helper: run git in a fixture repo."""
    return _run(repo, *args)
