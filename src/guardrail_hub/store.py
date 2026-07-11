"""In-process cache over the scanners — the web layer's only data entry point.

No database: everything derives from the checkouts, so the cache key derives
from checkout state. Snapshots key on ``(HEAD sha, mtime(metrics.json),
mtime(architecture.toml))`` — the mtimes catch a regenerated-but-uncommitted
``docs/generated/`` that HEAD alone would miss. Histories key on the default
branch tip. ``refresh()`` simply forgets, so the next access re-reads disk.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from guardrail_hub import budget_ledger, gitio, history, repo_scan
from guardrail_hub.config import HubConfig
from guardrail_hub.errors import RepoAccessError
from guardrail_hub.models import BudgetEvent, MetricPoint, RepoEntry, RepoSnapshot

_SnapshotKey = tuple[str, float, float]


def _mtime(entry: RepoEntry, rel: str) -> float:
    try:
        return (entry.root / rel).stat().st_mtime
    except OSError:
        return 0.0


def _snapshot_key(entry: RepoEntry) -> _SnapshotKey:
    try:
        sha = gitio.head_sha(entry.path)
    except RepoAccessError:
        sha = ""
    return (
        sha,
        _mtime(entry, "docs/generated/metrics.json"),
        _mtime(entry, repo_scan.ARCHITECTURE_TOML),
    )


class RepoStore:
    """Cached access to snapshots and histories for every registered repo."""

    def __init__(self, config: HubConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._snapshots: dict[str, tuple[_SnapshotKey, RepoSnapshot]] = {}
        self._histories: dict[str, tuple[str, tuple[MetricPoint, ...]]] = {}
        self._ledgers: dict[str, tuple[str, tuple[BudgetEvent, ...]]] = {}

    @property
    def entries(self) -> tuple[RepoEntry, ...]:
        return self._config.repos

    def entry(self, name: str) -> RepoEntry | None:
        return self._config.repo(name)

    def snapshot(self, entry: RepoEntry) -> RepoSnapshot:
        key = _snapshot_key(entry)
        with self._lock:
            cached = self._snapshots.get(entry.name)
            if cached is not None and cached[0] == key:
                return cached[1]
        snapshot = repo_scan.scan(entry)
        with self._lock:
            self._snapshots[entry.name] = (key, snapshot)
        return snapshot

    def history(self, entry: RepoEntry) -> tuple[MetricPoint, ...]:
        """Metric series from the default branch (HEAD fallback), cached by tip sha."""
        try:
            tip = gitio.branch_tip(entry.path, entry.default_branch) or gitio.head_sha(entry.path)
            ref = tip
        except RepoAccessError:
            return ()
        with self._lock:
            cached = self._histories.get(entry.name)
            if cached is not None and cached[0] == tip:
                return cached[1]
        try:
            points = history.mine_history(entry, ref)
        except RepoAccessError:
            points = ()
        with self._lock:
            self._histories[entry.name] = (tip, points)
        return points

    def ledger(self, entry: RepoEntry) -> tuple[BudgetEvent, ...]:
        """Budget-change events from the default branch, cached by tip sha."""
        try:
            tip = gitio.branch_tip(entry.path, entry.default_branch) or gitio.head_sha(entry.path)
        except RepoAccessError:
            return ()
        with self._lock:
            cached = self._ledgers.get(entry.name)
            if cached is not None and cached[0] == tip:
                return cached[1]
        try:
            events = budget_ledger.mine_ledger(entry, tip)
        except RepoAccessError:
            events = ()
        with self._lock:
            self._ledgers[entry.name] = (tip, events)
        return events

    def refresh(self, name: str | None = None) -> None:
        """Forget cached state (one repo, or everything) so next access re-reads disk."""
        with self._lock:
            if name is None:
                self._snapshots.clear()
                self._histories.clear()
                self._ledgers.clear()
            else:
                self._snapshots.pop(name, None)
                self._histories.pop(name, None)
                self._ledgers.pop(name, None)


StoreFactory = Callable[[HubConfig], RepoStore]
