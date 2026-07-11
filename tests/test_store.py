"""Unit tests for the RepoStore cache: key invalidation and refresh."""

from __future__ import annotations

import json
import os
from pathlib import Path

from tests.conftest import commit_snapshot, git, make_metrics, make_monorepo

from guardrail_hub.config import HubConfig, ServerConfig
from guardrail_hub.models import RepoEntry
from guardrail_hub.store import RepoStore


def _store(entry: RepoEntry) -> RepoStore:
    return RepoStore(HubConfig(repos=(entry,), server=ServerConfig()))


def _touch(path: Path) -> None:
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 10))


def test_snapshot_cached_until_commit(fixture_entry: RepoEntry) -> None:
    store = _store(fixture_entry)

    first = store.snapshot(fixture_entry)
    assert store.snapshot(fixture_entry) is first  # same key -> cached object

    commit_snapshot(fixture_entry.path, make_metrics(schema=2), message="bump")
    second = store.snapshot(fixture_entry)

    assert second is not first
    assert second.schema == 2


def test_snapshot_invalidated_by_uncommitted_regen(fixture_entry: RepoEntry) -> None:
    store = _store(fixture_entry)
    first = store.snapshot(fixture_entry)

    metrics_path = fixture_entry.path / "docs" / "generated" / "metrics.json"
    _touch(metrics_path)  # regenerated docs without a commit: mtime changes, HEAD doesn't

    assert store.snapshot(fixture_entry) is not first


def test_refresh_drops_cache(fixture_entry: RepoEntry) -> None:
    store = _store(fixture_entry)
    first = store.snapshot(fixture_entry)

    store.refresh()

    assert store.snapshot(fixture_entry) is not first


def test_history_from_default_branch_when_on_feature(fixture_entry: RepoEntry) -> None:
    store = _store(fixture_entry)
    git(fixture_entry.path, "checkout", "-q", "-b", "feature/x")
    commit_snapshot(fixture_entry.path, make_metrics(schema=99), message="feature edit")

    points = store.history(fixture_entry)

    assert len(points) == 1  # only main's snapshot; the feature commit is invisible
    assert points[0].metrics["schema"] == 1


def test_history_cached_by_tip(fixture_entry: RepoEntry) -> None:
    store = _store(fixture_entry)
    first = store.history(fixture_entry)

    assert store.history(fixture_entry) is first

    commit_snapshot(fixture_entry.path, make_metrics(schema=3), message="advance main")

    assert len(store.history(fixture_entry)) == 2


def test_history_missing_repo_is_empty(tmp_path: Path) -> None:
    entry = RepoEntry(name="ghost", path=tmp_path / "nope", family="test")

    assert _store(entry).history(entry) == ()


def test_snapshot_invalidated_by_subdir_regen(tmp_path: Path) -> None:
    repo = make_monorepo(tmp_path, subdir="server")
    entry = RepoEntry(name="mono-server", path=repo, subdir="server")
    store = _store(entry)
    assert store.snapshot(entry).metrics is not None

    path = repo / "server" / "docs" / "generated" / "metrics.json"
    path.write_text(json.dumps(make_metrics(size={"total_sloc": 999})), encoding="utf-8")
    _touch(path)

    snapshot = store.snapshot(entry)
    assert snapshot.metrics is not None and snapshot.metrics["size"]["total_sloc"] == 999
