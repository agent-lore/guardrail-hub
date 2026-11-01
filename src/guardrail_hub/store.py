"""In-process cache over the scanners — the web layer's only data entry point.

No database: everything derives from the checkouts, so the cache key derives
from checkout state. Snapshots key on ``(HEAD sha, mtime(metrics.json),
mtime(architecture.toml))`` — the mtimes catch a regenerated-but-uncommitted
``docs/generated/`` that HEAD alone would miss. Histories key on the default
branch tip. Dirty worktrees bypass snapshot caching so source-only edits update
the freshness badge even when generated files have not changed.

With ``[checkouts]`` enabled, ``entries``/``entry()`` hand out entries whose
``path`` points at the hub-managed clone, so every reader downstream works on
committed default-branch state; ``refresh()`` then fetches the clone before
forgetting caches. The managed-status badges (``fetched_at``/``fetch_error``)
are not part of the snapshot cache key — they change exactly when ``refresh``
clears the caches, so they re-render then. Disabled, ``refresh()`` degenerates
to today's clear-only semantics and never touches the working copies.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from guardrail_hub import (
    budget_ledger,
    checkouts,
    coupling,
    docs_render,
    gitio,
    history,
    hotspots,
    repo_scan,
    review,
)
from guardrail_hub.config import HubConfig
from guardrail_hub.errors import RepoAccessError
from guardrail_hub.models import (
    BudgetEvent,
    CouplingPair,
    MetricPoint,
    ModuleHotspot,
    RepoEntry,
    RepoSnapshot,
)

_SnapshotKey = tuple[str, float, float, bool]


def _mtime(entry: RepoEntry, rel: str) -> float:
    try:
        return (entry.root / rel).stat().st_mtime
    except OSError:
        return 0.0


def _snapshot_key(entry: RepoEntry) -> _SnapshotKey:
    try:
        sha = gitio.head_sha(entry.path)
        dirty = gitio.is_dirty(entry.path)
    except RepoAccessError:
        sha, dirty = "", False
    return (
        sha,
        _mtime(entry, "docs/generated/metrics.json"),
        _mtime(entry, repo_scan.ARCHITECTURE_TOML),
        dirty,
    )


class RepoStore:
    """Cached access to snapshots and histories for every registered repo."""

    def __init__(self, config: HubConfig) -> None:
        self._config = config
        self._resolved = {e.name: checkouts.resolve(config.checkouts, e) for e in config.repos}
        self._lock = threading.Lock()
        self._fetch_errors: dict[str, str] = {}
        # Locks key on the CHECKOUT PATH, not the entry name: monorepo subdir
        # entries share one clone, and two concurrent refreshes of the same
        # clone dir would race clone-vs-clone (seen as "Directory not empty").
        self._fetch_locks: dict[str, threading.Lock] = {}
        for e in config.repos:
            self._fetch_locks.setdefault(str(e.path), threading.Lock())
        self._snapshots: dict[str, tuple[_SnapshotKey, RepoSnapshot]] = {}
        self._histories: dict[str, tuple[str, tuple[MetricPoint, ...]]] = {}
        self._ledgers: dict[str, tuple[str, tuple[BudgetEvent, ...]]] = {}
        self._couplings: dict[str, tuple[str, tuple[CouplingPair, ...]]] = {}
        self._hotspots: dict[str, tuple[str, tuple[ModuleHotspot, ...]]] = {}

    @property
    def entries(self) -> tuple[RepoEntry, ...]:
        return tuple(self._resolved[e.name] for e in self._config.repos)

    def entry(self, name: str) -> RepoEntry | None:
        return self._resolved.get(name)

    def snapshot(self, entry: RepoEntry) -> RepoSnapshot:
        key = _snapshot_key(entry)
        with self._lock:
            cached = self._snapshots.get(entry.name)
            if cached is not None and cached[0] == key and not key[3]:
                return cached[1]
        snapshot = repo_scan.scan(entry)
        if self._config.checkouts.enabled:
            with self._lock:
                fetch_error = self._fetch_errors.get(entry.name, "")
            status = replace(
                snapshot.status,
                managed=True,
                fetched_at=checkouts.last_fetch(entry.path),
                fetch_error=fetch_error,
            )
            snapshot = replace(snapshot, status=status)
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

    def coupling(self, entry: RepoEntry) -> tuple[CouplingPair, ...]:
        """Cross-component co-change pairs from the default branch, cached by tip sha."""
        try:
            tip = gitio.branch_tip(entry.path, entry.default_branch) or gitio.head_sha(entry.path)
        except RepoAccessError:
            return ()
        with self._lock:
            cached = self._couplings.get(entry.name)
            if cached is not None and cached[0] == tip:
                return cached[1]
        try:
            pairs = coupling.mine_coupling(entry, tip)
        except RepoAccessError:
            pairs = ()
        with self._lock:
            self._couplings[entry.name] = (tip, pairs)
        return pairs

    def hotspots(self, entry: RepoEntry) -> tuple[ModuleHotspot, ...]:
        """Module hotspots from the default branch, cached by tip sha.

        Enrichment (SLOC, complexity) comes from the current snapshot; on an
        unmanaged worktree that can lag an uncommitted regeneration until the
        next refresh — the churn side is pure committed history either way.
        """
        try:
            tip = gitio.branch_tip(entry.path, entry.default_branch) or gitio.head_sha(entry.path)
        except RepoAccessError:
            return ()
        with self._lock:
            cached = self._hotspots.get(entry.name)
            if cached is not None and cached[0] == tip:
                return cached[1]
        metrics = self.snapshot(entry).metrics
        try:
            rows = hotspots.mine_hotspots(entry, tip, metrics)
        except RepoAccessError:
            rows = ()
        with self._lock:
            self._hotspots[entry.name] = (tip, rows)
        return rows

    def revision_file(self, entry: RepoEntry, revision: str, path: str) -> str:
        target = docs_render.contained(entry.root, entry.root / path)
        if target is None or ".git" in Path(path).parts:
            raise RepoAccessError("file is outside the project")
        sha = gitio.resolve_commit(entry.path, revision)
        relative = target.relative_to(entry.root.resolve()).as_posix()
        relative = f"{entry.subdir}/{relative}" if entry.subdir else relative
        if gitio.file_size(entry.path, sha, relative) > 2_000_000:
            raise RepoAccessError("file exceeds display limit")
        return gitio.show_file(entry.path, sha, relative)

    def comparison(self, entry: RepoEntry, base: str, head: str) -> dict:
        return review.compare(entry, base, head)

    def quality(self, entry: RepoEntry) -> dict:
        snapshot = self.snapshot(entry)
        result = review.current_report(dict(snapshot.metrics or {}), dict(snapshot.architecture))
        if snapshot.measurement_status in {"stale", "invalid"}:
            evidence = dict(result.get("quality", {}))
            for key in ("branch_coverage", "mutation"):
                evidence[key] = {
                    **evidence.get(key, {}),
                    "status": snapshot.measurement_status,
                    "value": None,
                }
            result["quality"] = evidence
        return result

    def refresh(self, name: str | None = None) -> None:
        """Fetch managed clones (when enabled), then forget cached state.

        One repo by name, or everything. Fetch failures land in the managed
        status badges via ``_fetch_errors`` — never raised.
        """
        names = [e.name for e in self._config.repos] if name is None else [name]
        for repo_name in names:
            self._fetch_one(repo_name)
        with self._lock:
            for repo_name in names:
                self._snapshots.pop(repo_name, None)
                self._histories.pop(repo_name, None)
                self._ledgers.pop(repo_name, None)
                self._couplings.pop(repo_name, None)
                self._hotspots.pop(repo_name, None)

    def _fetch_one(self, name: str) -> None:
        original = self._config.repo(name)
        if original is None or not self._config.checkouts.enabled:
            return
        with self._fetch_locks[str(original.path)]:
            error = checkouts.refresh(self._config.checkouts, original)
        with self._lock:
            self._fetch_errors[name] = error


StoreFactory = Callable[[HubConfig], RepoStore]
