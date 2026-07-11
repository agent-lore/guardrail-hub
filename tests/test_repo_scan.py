"""Unit tests for the worktree scanner."""

from __future__ import annotations

import json
from pathlib import Path

from tests.conftest import git, make_metrics, make_monorepo

from guardrail_hub.models import RepoEntry
from guardrail_hub.repo_scan import scan


def test_scan_healthy_repo(fixture_entry: RepoEntry) -> None:
    snapshot = scan(fixture_entry)

    assert snapshot.status.present and snapshot.status.is_git
    assert snapshot.status.branch == "main"
    assert snapshot.status.on_default_branch and not snapshot.status.dirty
    assert snapshot.schema == 1
    assert snapshot.has_metrics
    assert {b.key for b in snapshot.budgets} == {
        "cross_component_edges",
        "component_cycles",
        "modules_over_800_lines",
        "max_module_lines",
    }
    assert [c.name for c in snapshot.components] == ["Core", "Web"]
    assert snapshot.components[0].tier == "Core"
    assert snapshot.components[1].tier == "Entrypoints"
    assert "metrics.json" not in snapshot.docs  # only .md files listed


def test_scan_missing_checkout(tmp_path: Path) -> None:
    entry = RepoEntry(name="ghost", path=tmp_path / "nope", family="test")

    snapshot = scan(entry)

    assert not snapshot.status.present
    assert "not found" in snapshot.status.error
    assert not snapshot.has_metrics and snapshot.budgets == ()


def test_scan_plain_directory(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    snapshot = scan(RepoEntry(name="plain", path=plain, family="test"))

    assert snapshot.status.present and not snapshot.status.is_git
    assert "not a git repository" in snapshot.status.error


def test_scan_feature_branch_and_dirty(fixture_entry: RepoEntry) -> None:
    git(fixture_entry.path, "checkout", "-q", "-b", "feature/x")
    (fixture_entry.path / "scratch.txt").write_text("x", encoding="utf-8")

    snapshot = scan(fixture_entry)

    assert snapshot.status.branch == "feature/x"
    assert not snapshot.status.on_default_branch
    assert snapshot.status.dirty
    assert snapshot.has_metrics  # badges, not gates


def test_scan_repo_without_metrics(fixture_entry: RepoEntry) -> None:
    (fixture_entry.path / "docs" / "generated" / "metrics.json").unlink()

    snapshot = scan(fixture_entry)

    assert not snapshot.has_metrics
    assert "no metrics.json" in snapshot.status.error
    assert all(b.level == "unknown" for b in snapshot.budgets)


def test_scan_malformed_metrics(fixture_entry: RepoEntry) -> None:
    path = fixture_entry.path / "docs" / "generated" / "metrics.json"
    path.write_text("{broken", encoding="utf-8")

    snapshot = scan(fixture_entry)

    assert not snapshot.has_metrics
    assert "unreadable metrics.json" in snapshot.status.error


def test_scan_reads_worktree_not_head(fixture_entry: RepoEntry) -> None:
    path = fixture_entry.path / "docs" / "generated" / "metrics.json"
    path.write_text(json.dumps(make_metrics(schema=42)), encoding="utf-8")

    snapshot = scan(fixture_entry)

    assert snapshot.schema == 42  # uncommitted edit is visible ("now" = worktree)


def test_scan_subdir_entry_reads_under_subdir(tmp_path: Path) -> None:
    repo = make_monorepo(tmp_path, subdir="server")
    entry = RepoEntry(name="mono-server", path=repo, family="test", subdir="server")

    snapshot = scan(entry)

    assert snapshot.status.error == ""
    assert snapshot.has_metrics
    assert snapshot.budgets  # architecture.toml found under the subdir
    assert "metrics.json" not in snapshot.status.error


def test_scan_missing_subdir_is_badge_not_crash(tmp_path: Path) -> None:
    repo = make_monorepo(tmp_path, subdir="server")
    entry = RepoEntry(name="mono-client", path=repo, family="test", subdir="client")

    snapshot = scan(entry)

    assert not snapshot.has_metrics
    assert "subdir 'client' not found" in snapshot.status.error
