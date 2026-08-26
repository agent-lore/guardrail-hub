"""Unit tests for hub-managed clones, against real fixture repos."""

from __future__ import annotations

import shutil
from pathlib import Path

from tests.conftest import clone_of, commit_snapshot, git, make_metrics, make_repo

from guardrail_hub import checkouts, gitio
from guardrail_hub.config import CheckoutsConfig
from guardrail_hub.models import RepoEntry


def _entry(work: Path, **kwargs: str) -> RepoEntry:
    return RepoEntry(name="fixture", path=work, family="test", **kwargs)


def test_clone_dir_is_deterministic_and_distinct(tmp_path: Path) -> None:
    base = tmp_path / "base"

    a = checkouts.clone_dir(base, Path("/home/x/projects/repo"))
    b = checkouts.clone_dir(base, Path("/home/x/elsewhere/repo"))

    assert a == checkouts.clone_dir(base, Path("/home/x/projects/repo"))
    assert a != b  # same basename, different checkout
    assert a.name.startswith("repo-") and b.name.startswith("repo-")


def test_clone_dir_sanitizes_slug(tmp_path: Path) -> None:
    weird = checkouts.clone_dir(tmp_path, Path("/tmp/my repo (v2)"))

    assert "/" not in weird.name and " " not in weird.name


def test_monorepo_entries_share_one_clone(tmp_path: Path) -> None:
    cfg = CheckoutsConfig(enabled=True, dir=tmp_path / "data")
    server = RepoEntry(name="mono-server", path=Path("/tmp/mono"), subdir="server")
    client = RepoEntry(name="mono-client", path=Path("/tmp/mono"), subdir="client")

    resolved_server = checkouts.resolve(cfg, server)
    resolved_client = checkouts.resolve(cfg, client)

    assert resolved_server.path == resolved_client.path
    assert resolved_server.root == resolved_server.path / "server"
    assert resolved_client.root == resolved_client.path / "client"


def test_resolve_disabled_is_identity(tmp_path: Path) -> None:
    entry = _entry(tmp_path / "work")

    assert checkouts.resolve(CheckoutsConfig(enabled=False), entry) is entry


def test_resolve_enabled_preserves_fields(tmp_path: Path) -> None:
    cfg = CheckoutsConfig(enabled=True, dir=tmp_path / "data")
    entry = RepoEntry(name="fixture", path=tmp_path / "work", family="test", default_branch="trunk")

    resolved = checkouts.resolve(cfg, entry)

    assert resolved.path == checkouts.clone_dir(cfg.dir, entry.path)
    assert (resolved.name, resolved.family, resolved.default_branch) == (
        "fixture",
        "test",
        "trunk",
    )


def test_refresh_disabled_is_noop(tmp_path: Path) -> None:
    cfg = CheckoutsConfig(enabled=False, dir=tmp_path / "data")

    assert checkouts.refresh(cfg, _entry(tmp_path / "missing")) == ""
    assert not (tmp_path / "data").exists()


def test_refresh_clones_then_updates(tmp_path: Path) -> None:
    origin = make_repo(tmp_path)
    work = clone_of(origin, tmp_path / "work")
    cfg = CheckoutsConfig(enabled=True, dir=tmp_path / "data")
    entry = _entry(work)
    clone = checkouts.clone_dir(cfg.dir, work)

    assert checkouts.refresh(cfg, entry) == ""
    assert gitio.head_sha(clone) == gitio.head_sha(origin)

    commit_snapshot(origin, make_metrics(schema=2), message="origin moves on")
    assert checkouts.refresh(cfg, entry) == ""
    assert gitio.head_sha(clone) == gitio.head_sha(origin)


def test_refresh_reads_origin_not_working_copy(tmp_path: Path) -> None:
    origin = make_repo(tmp_path)
    work = clone_of(origin, tmp_path / "work")
    cfg = CheckoutsConfig(enabled=True, dir=tmp_path / "data")
    git(work, "checkout", "-q", "-b", "feature")
    commit_snapshot(work, make_metrics(schema=99), message="feature work")

    assert checkouts.refresh(cfg, _entry(work)) == ""

    clone = checkouts.clone_dir(cfg.dir, work)
    assert gitio.head_sha(clone) == gitio.head_sha(origin)
    assert gitio.current_branch(clone) == "main"


def test_refresh_reports_missing_origin_remote(tmp_path: Path) -> None:
    work = make_repo(tmp_path)  # no origin remote configured
    cfg = CheckoutsConfig(enabled=True, dir=tmp_path / "data")

    error = checkouts.refresh(cfg, _entry(work))

    assert "cannot discover origin" in error


def test_refresh_reports_missing_working_copy(tmp_path: Path) -> None:
    cfg = CheckoutsConfig(enabled=True, dir=tmp_path / "data")

    error = checkouts.refresh(cfg, _entry(tmp_path / "gone"))

    assert error != ""


def test_refresh_degrades_offline_keeping_clone(tmp_path: Path) -> None:
    origin = make_repo(tmp_path)
    work = clone_of(origin, tmp_path / "work")
    cfg = CheckoutsConfig(enabled=True, dir=tmp_path / "data")
    entry = _entry(work)
    assert checkouts.refresh(cfg, entry) == ""

    shutil.rmtree(origin)
    error = checkouts.refresh(cfg, entry)

    clone = checkouts.clone_dir(cfg.dir, work)
    assert error != ""  # fetch failed
    assert gitio.is_git_repo(clone)  # last-fetched state still served
    assert (clone / "docs" / "generated" / "metrics.json").is_file()


def test_refresh_recovers_interrupted_clone(tmp_path: Path) -> None:
    origin = make_repo(tmp_path)
    work = clone_of(origin, tmp_path / "work")
    cfg = CheckoutsConfig(enabled=True, dir=tmp_path / "data")
    stale = checkouts.clone_dir(cfg.dir, work)
    stale.mkdir(parents=True)
    (stale / "half-written").write_text("junk", encoding="utf-8")

    assert checkouts.refresh(cfg, _entry(work)) == ""
    assert gitio.is_git_repo(stale)
    assert not (stale / "half-written").exists()


def test_refresh_follows_repointed_origin(tmp_path: Path) -> None:
    origin_a = make_repo(tmp_path / "a")
    work = clone_of(origin_a, tmp_path / "work")
    cfg = CheckoutsConfig(enabled=True, dir=tmp_path / "data")
    entry = _entry(work)
    assert checkouts.refresh(cfg, entry) == ""

    origin_b = make_repo(tmp_path / "b", snapshots=[make_metrics(schema=7)])
    git(work, "remote", "set-url", "origin", str(origin_b))

    assert checkouts.refresh(cfg, entry) == ""
    clone = checkouts.clone_dir(cfg.dir, work)
    assert gitio.remote_url(clone) == str(origin_b)
    assert gitio.head_sha(clone) == gitio.head_sha(origin_b)


def test_last_fetch(tmp_path: Path) -> None:
    origin = make_repo(tmp_path)
    work = clone_of(origin, tmp_path / "work")
    cfg = CheckoutsConfig(enabled=True, dir=tmp_path / "data")
    clone = checkouts.clone_dir(cfg.dir, work)

    assert checkouts.last_fetch(clone) == ""  # no clone yet

    assert checkouts.refresh(cfg, _entry(work)) == ""
    stamp = checkouts.last_fetch(clone)
    assert stamp.endswith("UTC") and len(stamp) == len("2026-08-26 12:00 UTC")
