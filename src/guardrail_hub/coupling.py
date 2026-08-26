"""Change coupling: cross-component module pairs that change together.

Mines the first-parent history for commits whose changed source files map to
modules in *different* components (via :mod:`guardrail_hub.archmap`). High
co-change across a component seam is the inverse of locality — the seam is in
the wrong place, or the contract between the components is leaking. Companion
to the hotspots panel; a review lens, deliberately not a budget.
"""

from __future__ import annotations

import datetime
from collections import Counter
from itertools import combinations

from guardrail_hub import archmap, gitio
from guardrail_hub.hotspots import MAX_COMMIT_MODULES, decay_weight
from guardrail_hub.models import CouplingPair, RepoEntry

MAX_COMMITS = 1000
MIN_CO_CHANGES = 3
TOP_PAIRS = 20


def mine_coupling(entry: RepoEntry, ref: str) -> tuple[CouplingPair, ...]:
    """Top cross-component co-change pairs on ``ref``'s first-parent history.

    ``weighted_co`` decay-weights each co-change by commit recency (same
    half-life and newest-commit anchor as the hotspot view) so recently
    coupled pairs sort first; the MIN_CO_CHANGES threshold stays on the raw
    count, so an old-but-real coupling still appears.
    """
    arch = archmap.load_archmap(entry)
    if arch is None:
        return ()
    commits = gitio.first_parent_numstat(entry.path, ref, MAX_COMMITS)
    dates = [datetime.date.fromisoformat(date) for date, _ in commits]
    if not dates:
        return ()
    newest = max(dates)

    changes: Counter[str] = Counter()
    pair_counts: Counter[tuple[str, str]] = Counter()
    pair_weights: dict[tuple[str, str], float] = {}
    for date, (_, files) in zip(dates, commits, strict=True):
        modules = {m for path, _ in files if (m := arch.module_of(path)) is not None}
        if not modules or len(modules) > MAX_COMMIT_MODULES:
            continue
        weight = decay_weight((newest - date).days)
        changes.update(modules)
        for pair in combinations(sorted(modules), 2):
            pair_counts[pair] += 1
            pair_weights[pair] = pair_weights.get(pair, 0.0) + weight

    pairs: list[CouplingPair] = []
    for (mod_a, mod_b), co in pair_counts.items():
        if co < MIN_CO_CHANGES:
            continue
        comp_a, comp_b = arch.component_of(mod_a), arch.component_of(mod_b)
        if comp_a is None or comp_b is None or comp_a == comp_b:
            continue
        pairs.append(
            CouplingPair(
                module_a=mod_a,
                component_a=comp_a,
                module_b=mod_b,
                component_b=comp_b,
                co_changes=co,
                changes_a=changes[mod_a],
                changes_b=changes[mod_b],
                strength=round(co / min(changes[mod_a], changes[mod_b]), 2),
                weighted_co=round(pair_weights[(mod_a, mod_b)], 2),
            )
        )
    pairs.sort(key=lambda p: (-p.weighted_co, -p.co_changes, -p.strength, p.module_a, p.module_b))
    return tuple(pairs[:TOP_PAIRS])
