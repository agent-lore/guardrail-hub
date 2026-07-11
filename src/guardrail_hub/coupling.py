"""Change coupling: cross-component module pairs that change together.

Mines the first-parent history for commits whose changed source files map to
modules in *different* components (via :mod:`guardrail_hub.archmap`). High
co-change across a component seam is the inverse of locality — the seam is in
the wrong place, or the contract between the components is leaking. Companion
to the hotspots panel; a review lens, deliberately not a budget.
"""

from __future__ import annotations

from collections import Counter
from itertools import combinations

from guardrail_hub import archmap, gitio
from guardrail_hub.models import CouplingPair, RepoEntry

MAX_COMMITS = 1000
# Mega-commits (mass refactors, formatting sweeps) couple everything to
# everything and would drown the signal (Tornhill's standard guard).
MAX_COMMIT_MODULES = 25
MIN_CO_CHANGES = 3
TOP_PAIRS = 20


def mine_coupling(entry: RepoEntry, ref: str) -> tuple[CouplingPair, ...]:
    """Top cross-component co-change pairs on ``ref``'s first-parent history."""
    arch = archmap.load_archmap(entry)
    if arch is None:
        return ()
    changes: Counter[str] = Counter()
    pair_counts: Counter[tuple[str, str]] = Counter()
    for paths in gitio.first_parent_changes(entry.path, ref, MAX_COMMITS):
        modules = {m for p in paths if (m := arch.module_of(p)) is not None}
        if not modules or len(modules) > MAX_COMMIT_MODULES:
            continue
        changes.update(modules)
        pair_counts.update(combinations(sorted(modules), 2))

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
            )
        )
    pairs.sort(key=lambda p: (-p.co_changes, -p.strength, p.module_a, p.module_b))
    return tuple(pairs[:TOP_PAIRS])
