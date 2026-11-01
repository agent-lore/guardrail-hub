"""Unit tests for the RepoStore cache: key invalidation and refresh."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from tests.conftest import (
    clone_of,
    commit_files,
    commit_snapshot,
    git,
    make_metrics,
    make_monorepo,
    make_repo,
)

from guardrail_hub import checkouts
from guardrail_hub.config import CheckoutsConfig, HubConfig, ServerConfig
from guardrail_hub.models import RepoEntry
from guardrail_hub.store import RepoStore


def _store(entry: RepoEntry) -> RepoStore:
    return RepoStore(HubConfig(repos=(entry,), server=ServerConfig()))


def _managed_store(tmp_path: Path) -> tuple[RepoStore, Path, Path]:
    """A store with managed checkouts on: (store, origin repo, working copy)."""
    origin = make_repo(tmp_path)
    work = clone_of(origin, tmp_path / "work")
    entry = RepoEntry(name="fixture", path=work, family="test")
    config = HubConfig(
        repos=(entry,),
        server=ServerConfig(),
        checkouts=CheckoutsConfig(enabled=True, dir=tmp_path / "data"),
    )
    return RepoStore(config), origin, work


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


def test_managed_entries_resolve_to_clone_paths(tmp_path: Path) -> None:
    store, _origin, work = _managed_store(tmp_path)

    entry = store.entry("fixture")
    assert entry is not None
    assert entry.path == checkouts.clone_dir(tmp_path / "data", work)
    assert store.entries == (entry,)


def test_unmanaged_entries_are_config_entries(fixture_entry: RepoEntry) -> None:
    store = _store(fixture_entry)

    assert store.entries == (fixture_entry,)
    assert store.entry("fixture") == fixture_entry


def test_managed_snapshot_reads_clone_not_working_copy(tmp_path: Path) -> None:
    store, origin, work = _managed_store(tmp_path)
    store.refresh()
    entry = store.entries[0]

    # feature-branch churn in the working copy is invisible to the dashboard
    git(work, "checkout", "-q", "-b", "feature")
    commit_snapshot(work, make_metrics(schema=99), message="feature edit")
    snapshot = store.snapshot(entry)
    assert snapshot.schema == 1
    assert snapshot.status.managed is True
    assert snapshot.status.branch == "main"
    assert not snapshot.status.dirty
    assert snapshot.status.fetched_at != ""
    assert snapshot.status.fetch_error == ""

    # a commit on origin's main shows up after refresh
    commit_snapshot(origin, make_metrics(schema=2), message="mainline")
    store.refresh("fixture")
    assert store.snapshot(entry).schema == 2


def test_managed_refresh_records_fetch_error(tmp_path: Path) -> None:
    store, origin, _work = _managed_store(tmp_path)
    store.refresh()
    shutil.rmtree(origin)

    store.refresh()
    snapshot = store.snapshot(store.entries[0])

    assert snapshot.status.fetch_error != ""
    assert snapshot.has_metrics  # last-fetched state still renders


def test_managed_before_first_fetch_renders_error_card(tmp_path: Path) -> None:
    store, _origin, _work = _managed_store(tmp_path)

    snapshot = store.snapshot(store.entries[0])  # no refresh yet: clone absent

    assert snapshot.status.present is False
    assert "checkout not found" in snapshot.status.error


def test_unmanaged_refresh_never_fetches(fixture_entry: RepoEntry) -> None:
    store = _store(fixture_entry)
    first = store.snapshot(fixture_entry)

    store.refresh()

    assert store.snapshot(fixture_entry) is not first
    assert store.snapshot(fixture_entry).status.managed is False


def test_monorepo_entries_share_fetch_lock_and_refresh_concurrently(tmp_path: Path) -> None:
    """Two subdir entries over one checkout must not race clone-vs-clone.

    Mirrors the serve-startup fan-out: refresh(name) per entry, concurrently.
    """
    import threading

    origin = make_monorepo(tmp_path, subdir="server")
    work = clone_of(origin, tmp_path / "work")
    entries = (
        RepoEntry(name="mono-server", path=work, subdir="server"),
        RepoEntry(name="mono-client", path=work, subdir="client"),
    )
    config = HubConfig(
        repos=entries,
        server=ServerConfig(),
        checkouts=CheckoutsConfig(enabled=True, dir=tmp_path / "data"),
    )
    store = RepoStore(config)

    threads = [threading.Thread(target=store.refresh, args=(e.name,)) for e in entries]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    resolved = store.entries
    assert resolved[0].path == resolved[1].path  # one shared clone
    assert (resolved[0].path / ".git").is_dir()
    for entry in resolved:
        assert store.snapshot(entry).status.fetch_error == ""


def test_hotspots_cached_by_tip_and_refreshed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    entry = RepoEntry(name="fixture", path=repo, family="test")
    store = _store(entry)
    for i in range(2):
        commit_files(repo, {"src/fixture/core.py": f"a = {i}\n"}, message=f"core {i}")

    first = store.hotspots(entry)
    assert first and first[0].module == "fixture.core"
    assert store.hotspots(entry) is first  # cached by tip

    commit_files(repo, {"src/fixture/core.py": "a = 9\n"}, message="more churn")
    second = store.hotspots(entry)
    assert second is not first
    assert second[0].churn > first[0].churn


def test_hotspots_missing_repo_is_empty(tmp_path: Path) -> None:
    entry = RepoEntry(name="ghost", path=tmp_path / "nope", family="test")

    assert _store(entry).hotspots(entry) == ()


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
