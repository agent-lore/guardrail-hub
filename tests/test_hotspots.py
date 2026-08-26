"""Module-hotspot tests: numstat mining, decay, normalization, fallbacks."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import commit_files, git, make_metrics, make_repo

from guardrail_hub.hotspots import HALF_LIFE_DAYS, MAX_COMMIT_MODULES, decay_weight, mine_hotspots
from guardrail_hub.models import RepoEntry


def entry_for(repo: Path) -> RepoEntry:
    return RepoEntry(name="fixture", path=repo, family="test")


def _churn_history(repo: Path) -> None:
    """Three commits touching core (churn 1+2+2), one touching web (churn 1)."""
    for i in range(3):
        commit_files(repo, {"src/fixture/core.py": f"a = {i}\n"}, message=f"core {i}")
    commit_files(repo, {"src/fixture/web.py": "b = 0\n"}, message="web 0")


def test_decay_weight_half_life() -> None:
    assert decay_weight(0) == 1.0
    assert decay_weight(HALF_LIFE_DAYS) == 0.5
    assert decay_weight(2 * HALF_LIFE_DAYS) == 0.25


def test_missing_architecture_toml_is_empty(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / "docs" / "architecture.toml").unlink()

    assert mine_hotspots(entry_for(repo), "HEAD", make_metrics()) == ()


def test_no_source_history_is_empty(fixture_repo: Path) -> None:
    # the fixture's commits touch only docs/ — nothing maps to a module
    assert mine_hotspots(entry_for(fixture_repo), "HEAD", make_metrics()) == ()


def test_churn_ranks_and_normalizes(fixture_repo: Path) -> None:
    _churn_history(fixture_repo)

    rows = {r.module: r for r in mine_hotspots(entry_for(fixture_repo), "HEAD", make_metrics())}

    core, web = rows["fixture.core"], rows["fixture.web"]
    # same-day fixture commits -> decay weight exactly 1.0, churn == raw lines
    assert core.churn == 5.0  # +1, then 1+1 twice
    assert core.commits == 3
    assert (core.component, core.sloc, core.max_complexity) == ("Core", 200, 6)
    assert core.score == round(5.0 * 6 * 100 / 200, 1)
    assert web.churn == 1.0
    assert web.score == round(1.0 * 3 * 100 / 100, 1)
    assert core.score > web.score


def test_net_zero_refactor_still_counts(fixture_repo: Path) -> None:
    commit_files(fixture_repo, {"src/fixture/core.py": "a = 1\nb = 2\n"}, message="add")
    commit_files(fixture_repo, {"src/fixture/core.py": "a = 9\nb = 8\n"}, message="rewrite")

    rows = {r.module: r for r in mine_hotspots(entry_for(fixture_repo), "HEAD", make_metrics())}

    # the rewrite kept the line count identical (invisible to snapshot deltas)
    # but numstat sees 2 added + 2 deleted
    assert rows["fixture.core"].churn == 2.0 + 4.0


def test_deleted_module_is_not_a_hotspot(fixture_repo: Path) -> None:
    commit_files(fixture_repo, {"src/fixture/gone.py": "x = 1\n"}, message="add gone")
    git(fixture_repo, "rm", "-q", "src/fixture/gone.py")
    git(fixture_repo, "commit", "-q", "-m", "remove gone")

    rows = mine_hotspots(entry_for(fixture_repo), "HEAD", make_metrics())

    assert all(r.module != "fixture.gone" for r in rows)


def test_fallback_without_modules_map_uses_component_complexity(fixture_repo: Path) -> None:
    _churn_history(fixture_repo)
    metrics = make_metrics()
    del metrics["modules"]  # pre-1.5.0 snapshot shape

    rows = {r.module: r for r in mine_hotspots(entry_for(fixture_repo), "HEAD", metrics)}

    core = rows["fixture.core"]
    assert (core.sloc, core.max_complexity) == (0, 6)  # component-level fallback
    assert core.score == round(5.0 * 6, 1)  # raw product, no normalization


def test_no_metrics_at_all_ranks_by_raw_churn(fixture_repo: Path) -> None:
    _churn_history(fixture_repo)

    rows = {r.module: r for r in mine_hotspots(entry_for(fixture_repo), "HEAD", None)}

    assert rows["fixture.core"].score == 5.0  # max(1, 0) complexity guard
    assert rows["fixture.core"].max_complexity == 0


def test_mega_commits_are_skipped(fixture_repo: Path) -> None:
    _churn_history(fixture_repo)
    sweep = {f"src/fixture/web/mod{i:02d}.py": "x = 1\n" for i in range(MAX_COMMIT_MODULES)}
    commit_files(fixture_repo, {**sweep, "src/fixture/core.py": "a = 99\n"}, message="sweep")

    rows = {r.module: r for r in mine_hotspots(entry_for(fixture_repo), "HEAD", make_metrics())}

    assert rows["fixture.core"].commits == 3  # the sweep did not count
