"""Unit tests for the budget-change ledger miner."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import git, make_metrics, make_monorepo, make_repo

from guardrail_hub.budget_ledger import mine_ledger, raise_counts
from guardrail_hub.models import RepoEntry


def _commit_budgets(repo: Path, budgets: str, subdir: str = "", message: str = "budgets") -> None:
    docs = (repo / subdir if subdir else repo) / "docs"
    (docs / "architecture.toml").write_text(
        f'[project]\nroot_package = "fixture"\n\n[budgets]\n{budgets}\n', encoding="utf-8"
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message, "--no-verify")


def test_ledger_records_raises_lowers_adds_and_removals(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)  # baseline architecture.toml from the fixture
    _commit_budgets(repo, "cross_component_edges = 7\nmodule_cycles = 0")
    _commit_budgets(repo, "cross_component_edges = 9\nmodule_cycles = 0")  # raise
    _commit_budgets(repo, "cross_component_edges = 8\nmax_module_lines = 800")  # lower/add/remove
    entry = RepoEntry(name="fixture", path=repo)

    events = mine_ledger(entry, "main")

    described = [(e.key, e.old, e.new, e.kind) for e in events]
    assert ("cross_component_edges", 7, 9, "raise") in described
    assert ("cross_component_edges", 9, 8, "lower") in described
    assert ("max_module_lines", None, 800, "added") in described
    assert ("module_cycles", 0, None, "removed") in described


def test_first_snapshot_is_baseline_not_events(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)  # fixture toml already declares budgets

    events = mine_ledger(RepoEntry(name="fixture", path=repo), "main")

    assert events == ()  # adopting the kit is not a budget "event"


def test_malformed_snapshot_is_skipped(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    _commit_budgets(repo, "cross_component_edges = 7")
    (repo / "docs" / "architecture.toml").write_text("not [ valid toml", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "broken", "--no-verify")
    _commit_budgets(repo, "cross_component_edges = 9")

    events = mine_ledger(RepoEntry(name="fixture", path=repo), "main")

    # baseline(5)->7 then 7->9; the malformed middle commit is skipped, not a break
    assert [(e.old, e.new) for e in events if e.key == "cross_component_edges"] == [(5, 7), (7, 9)]


def test_subdir_entry_mines_under_subdir(tmp_path: Path) -> None:
    repo = make_monorepo(tmp_path, subdir="server", snapshots=[make_metrics()])
    _commit_budgets(repo, "cross_component_edges = 5", subdir="server")
    _commit_budgets(repo, "cross_component_edges = 6", subdir="server")
    entry = RepoEntry(name="mono-server", path=repo, subdir="server")

    events = mine_ledger(entry, "main")

    assert [(e.key, e.kind) for e in events][-1] == ("cross_component_edges", "raise")


def test_raise_counts_only_counts_raises() -> None:
    import datetime

    from guardrail_hub.models import BudgetEvent

    day = datetime.date(2026, 7, 11)
    events = (
        BudgetEvent(key="edges", old=5, new=7, sha="a", date=day),
        BudgetEvent(key="edges", old=7, new=9, sha="b", date=day),
        BudgetEvent(key="edges", old=9, new=8, sha="c", date=day),  # lower
        BudgetEvent(key="cycles", old=None, new=0, sha="d", date=day),  # added
    )

    assert raise_counts(events) == {"edges": 2}
