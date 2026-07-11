"""Budget-raise ledger: every change to [budgets] in a repo's history.

Budgets are hand-edited ratchets, so their git history is the meta-metric:
a key that keeps getting raised is a weak ratchet (or a wrong budget), and a
falling one is locked-in improvement. Mined from first-parent history of
``docs/architecture.toml`` on the default branch, the same way the metric
time series is mined from ``metrics.json``.
"""

from __future__ import annotations

import datetime
import tomllib

from guardrail_hub import gitio
from guardrail_hub.errors import RepoAccessError
from guardrail_hub.models import BudgetEvent, RepoEntry

ARCHITECTURE_PATH = "docs/architecture.toml"


def architecture_path(entry: RepoEntry) -> str:
    """Repo-relative path of architecture.toml (subdir-aware for monorepos)."""
    return f"{entry.subdir}/{ARCHITECTURE_PATH}" if entry.subdir else ARCHITECTURE_PATH


def _budgets_at(entry: RepoEntry, sha: str, rel: str) -> dict[str, int] | None:
    """The [budgets] table at one commit, or None when unreadable/malformed."""
    try:
        raw = tomllib.loads(gitio.show_file(entry.path, sha, rel))
    except (RepoAccessError, tomllib.TOMLDecodeError):
        return None
    budgets = raw.get("budgets", {})
    if not isinstance(budgets, dict):
        return None
    return {
        key: value
        for key, value in budgets.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }


def mine_ledger(entry: RepoEntry, ref: str) -> tuple[BudgetEvent, ...]:
    """One BudgetEvent per key change between consecutive mainline snapshots.

    The first parseable snapshot is the baseline (adopting the kit is not an
    "event"); malformed or missing snapshots are skipped, matching the metric
    history walker.
    """
    rel = architecture_path(entry)
    events: list[BudgetEvent] = []
    previous: dict[str, int] | None = None
    for sha, date_str in gitio.first_parent_log(entry.path, ref, rel):
        budgets = _budgets_at(entry, sha, rel)
        if budgets is None:
            continue
        if previous is not None and budgets != previous:
            date = datetime.date.fromisoformat(date_str)
            for key in sorted(previous.keys() | budgets.keys()):
                old, new = previous.get(key), budgets.get(key)
                if old != new:
                    events.append(BudgetEvent(key=key, old=old, new=new, sha=sha, date=date))
        previous = budgets
    return tuple(events)


def raise_counts(events: tuple[BudgetEvent, ...]) -> dict[str, int]:
    """Raises per budget key — high counts mark weak ratchets."""
    counts: dict[str, int] = {}
    for event in events:
        if event.kind == "raise":
            counts[event.key] = counts.get(event.key, 0) + 1
    return counts
