"""Module hotspots: decay-weighted mainline churn where complexity lives.

Churn comes from real ``git log --numstat`` history (so a net-zero refactor
counts, unlike the old snapshot-delta derivation), weighted by recency with a
90-day half-life, and mapped to modules via :mod:`guardrail_hub.archmap` —
the same granularity as the change-coupling view. The score normalizes by
module SLOC where the snapshot carries per-module metrics (kit >= 1.5.0), so
"churn density x complexity" is roughly comparable across repos; older
snapshots fall back to the raw product with component-level complexity.

Determinism: commit age is measured against the newest commit in the mined
window — never wall clock — so the result is a pure function of ``ref`` and
cacheable by tip sha.
"""

from __future__ import annotations

import datetime
from collections import Counter
from collections.abc import Mapping
from typing import Any

from guardrail_hub import archmap, gitio
from guardrail_hub.models import ModuleHotspot, RepoEntry

HALF_LIFE_DAYS = 90
MAX_COMMITS = 1000
# Mega-commits (mass refactors, formatting sweeps) are activity, not risk
# signal (Tornhill's standard guard) — same threshold as the coupling view.
MAX_COMMIT_MODULES = 25

_Numstat = list[tuple[str, list[tuple[str, int]]]]


def decay_weight(age_days: int) -> float:
    """Recency weight: 1.0 for the newest commit, halving every 90 days."""
    return 0.5 ** (age_days / HALF_LIFE_DAYS)


def _module_churn(arch: archmap.ArchMap, files: list[tuple[str, int]]) -> dict[str, int]:
    churn: dict[str, int] = {}
    for path, lines in files:
        module = arch.module_of(path)
        if module is not None:
            churn[module] = churn.get(module, 0) + lines
    return churn


def _weighted_churn(
    arch: archmap.ArchMap, commits: _Numstat
) -> tuple[dict[str, float], Counter[str]]:
    """Decay-weighted churn and commit count per module, over the mined window."""
    dates = [datetime.date.fromisoformat(date) for date, _ in commits]
    newest = max(dates)
    churn: dict[str, float] = {}
    counts: Counter[str] = Counter()
    for date, (_, files) in zip(dates, commits, strict=True):
        per_module = _module_churn(arch, files)
        if not per_module or len(per_module) > MAX_COMMIT_MODULES:
            continue
        weight = decay_weight((newest - date).days)
        for module, lines in per_module.items():
            churn[module] = churn.get(module, 0.0) + weight * lines
            counts[module] += 1
    return churn, counts


def _mapping_at(metrics: Mapping[str, Any] | None, *keys: str) -> Mapping[str, Any] | None:
    value: Any = metrics
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value if isinstance(value, Mapping) else None


def _build_row(
    module: str,
    component: str,
    churn: float,
    commits: int,
    module_stats: Mapping[str, Any] | None,
    component_cx: Mapping[str, Any] | None,
) -> ModuleHotspot | None:
    if module_stats is not None:
        stats = _mapping_at(module_stats, module)
        if stats is None:
            return None  # deleted or unscanned module — not a current hotspot
        sloc = int(stats.get("sloc", 0))
        max_cx = int(stats.get("max_complexity", 0))
        # Churn per 100 SLOC, complexity-weighted: normalized so repos of
        # different sizes rank on the same scale. max(1, …) keeps
        # complexity-free modules (C++ pre-complexity) ranked by churn.
        score = round(churn * max(1, max_cx) * 100 / max(sloc, 1), 1)
    else:
        comp_stats = _mapping_at(component_cx, component) or {}
        sloc = 0
        max_cx = int(comp_stats.get("max_complexity", 0))
        # Pre-1.5.0 snapshot (no per-module map): raw product, internally
        # consistent within the repo but not comparable across repos.
        score = round(churn * max(1, max_cx), 1)
    return ModuleHotspot(
        module=module,
        component=component,
        churn=churn,
        commits=commits,
        sloc=sloc,
        max_complexity=max_cx,
        score=score,
        method="churn density x module complexity"
        if module_stats is not None
        else "legacy raw churn x component complexity",
    )


def mine_hotspots(
    entry: RepoEntry, ref: str, metrics: Mapping[str, Any] | None
) -> tuple[ModuleHotspot, ...]:
    """Hotspot rows on ``ref``'s first-parent history, hottest first.

    ``metrics`` is the repo's latest snapshot (for per-module SLOC and
    complexity). When the snapshot has a ``modules`` map, modules absent from
    it (deleted, or outside the scanned source root) are dropped — history
    can't make something that no longer exists a hotspot.
    """
    arch = archmap.load_archmap(entry)
    if arch is None:
        return ()
    commits = gitio.first_parent_numstat(entry.path, ref, MAX_COMMITS)
    if not commits:
        return ()
    churn, counts = _weighted_churn(arch, commits)
    module_stats = _mapping_at(metrics, "modules")
    component_cx = _mapping_at(metrics, "complexity", "components")
    rows = []
    for module in sorted(churn):
        row = _build_row(
            module,
            arch.component_of(module) or "",
            round(churn[module], 1),
            counts[module],
            module_stats,
            component_cx,
        )
        if row is not None:
            rows.append(row)
    return tuple(sorted(rows, key=lambda r: (-r.score, -r.churn, r.module)))
