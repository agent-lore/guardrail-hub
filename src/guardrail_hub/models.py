"""Frozen value types shared across the hub.

These are pure data — no I/O, no behaviour beyond trivial derived properties —
so every tier can import them without dragging in git or the web stack.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

BudgetLevel = Literal["ok", "tight", "breach", "unknown"]
DriftStatus = Literal["same", "differs", "missing", "extra", "error"]


@dataclass(frozen=True)
class RepoEntry:
    """One registered repo from the hub config.

    ``subdir`` supports monorepos: the kit instance (tests/guardrail/, docs/)
    lives under ``path/subdir`` while git operations stay on the checkout at
    ``path``. One checkout can register several entries with distinct subdirs.
    """

    name: str
    path: Path
    family: str = "default"
    default_branch: str = "main"
    subdir: str = ""

    @property
    def root(self) -> Path:
        """The project root the kit instance sees (== path unless subdir is set)."""
        return self.path / self.subdir if self.subdir else self.path


@dataclass(frozen=True)
class RepoStatus:
    """Where a repo checkout is right now (badges, not gates)."""

    present: bool
    is_git: bool = False
    branch: str = ""
    on_default_branch: bool = False
    dirty: bool = False
    head_sha: str = ""
    error: str = ""


@dataclass(frozen=True)
class MetricPoint:
    """One committed metrics.json snapshot from a repo's history."""

    sha: str
    date: datetime.date
    metrics: Mapping[str, Any]


@dataclass(frozen=True)
class BudgetStatus:
    """A single [budgets] entry compared against the measured metrics."""

    key: str
    budget: int
    actual: int | None
    headroom: int | None
    level: BudgetLevel


@dataclass(frozen=True)
class ComponentRow:
    """Per-component stats merged from the metrics snapshot's three sections."""

    name: str
    tier: str
    modules: int
    lines: int
    sloc: int
    fan_in: int
    fan_out: int
    instability: float | None
    max_complexity: int
    functions_over_10: int


@dataclass(frozen=True)
class RepoSnapshot:
    """Everything the dashboard shows for one repo's current worktree state."""

    entry: RepoEntry
    status: RepoStatus
    schema: int | None = None
    metrics: Mapping[str, Any] | None = None
    budgets: tuple[BudgetStatus, ...] = ()
    components: tuple[ComponentRow, ...] = ()
    docs: tuple[str, ...] = ()

    @property
    def has_metrics(self) -> bool:
        return self.metrics is not None


@dataclass(frozen=True)
class FileDrift:
    """Drift verdict for one kit file in one repo."""

    path: str
    role: str
    status: DriftStatus
    detail: str = ""


@dataclass(frozen=True)
class DriftReport:
    """Kit drift for one repo: canonical version vs what the repo has."""

    repo: str
    kit_version: str
    installed_version: str
    files: tuple[FileDrift, ...] = field(default_factory=tuple)

    @property
    def clean(self) -> bool:
        return all(f.status in ("same", "extra") for f in self.files)
