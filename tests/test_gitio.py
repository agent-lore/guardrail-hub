"""Unit tests for the git subprocess seam, against real fixture repos."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import commit_snapshot, git, make_metrics, make_repo

from guardrail_hub import gitio
from guardrail_hub.errors import RepoAccessError


def test_basic_state(fixture_repo: Path) -> None:
    assert gitio.is_git_repo(fixture_repo)
    assert gitio.current_branch(fixture_repo) == "main"
    assert len(gitio.head_sha(fixture_repo)) == 40
    assert not gitio.is_dirty(fixture_repo)


def test_not_a_repo(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    assert not gitio.is_git_repo(plain)
    with pytest.raises(RepoAccessError):
        gitio.head_sha(plain)


def test_dirty_detection(fixture_repo: Path) -> None:
    (fixture_repo / "scratch.txt").write_text("x", encoding="utf-8")

    assert gitio.is_dirty(fixture_repo)


def test_branch_tip(fixture_repo: Path) -> None:
    assert gitio.branch_tip(fixture_repo, "main") == gitio.head_sha(fixture_repo)
    assert gitio.branch_tip(fixture_repo, "nope") is None


def test_first_parent_log_and_show(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, snapshots=[make_metrics(schema=1), make_metrics(schema=2)])

    log = gitio.first_parent_log(repo, "main", "docs/generated/metrics.json")

    assert len(log) == 2
    sha, date = log[0]
    assert len(sha) >= 7 and len(date) == 10
    assert '"schema": 1' in gitio.show_file(repo, sha, "docs/generated/metrics.json")


def test_first_parent_excludes_side_branch(fixture_repo: Path) -> None:
    git(fixture_repo, "checkout", "-q", "-b", "side")
    commit_snapshot(fixture_repo, make_metrics(schema=99), message="side edit")
    git(fixture_repo, "checkout", "-q", "main")
    git(fixture_repo, "merge", "-q", "--no-ff", "-m", "merge side", "side")

    log = gitio.first_parent_log(fixture_repo, "main", "docs/generated/metrics.json")

    # initial snapshot + the merge commit itself; the side commit is not a point
    shas = [sha for sha, _ in log]
    side_sha = git(fixture_repo, "rev-parse", "--short", "side").strip()
    assert side_sha not in shas
    assert len(log) == 2


def test_show_missing_file_raises(fixture_repo: Path) -> None:
    sha = gitio.head_sha(fixture_repo)

    with pytest.raises(RepoAccessError):
        gitio.show_file(fixture_repo, sha, "does/not/exist.json")
