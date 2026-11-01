"""Worktree scanner: what a registered repo looks like right now.

Reads the checkout as-is (committed or not): ``docs/generated/metrics.json``
for the current snapshot, ``docs/architecture.toml`` for budgets and tiers, and
the ``docs/generated/`` listing for the doc viewer. Any per-repo problem lands
in ``RepoStatus.error`` — a broken or missing repo renders as a badge, never a
crash.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from guardrail_hub import gitio
from guardrail_hub.budgets import budget_statuses
from guardrail_hub.errors import RepoAccessError
from guardrail_hub.models import ComponentRow, RepoEntry, RepoSnapshot, RepoStatus

GENERATED_DIR = "docs/generated"
ARCHITECTURE_TOML = "docs/architecture.toml"


def _status(entry: RepoEntry) -> RepoStatus:
    if not entry.path.is_dir():
        return RepoStatus(present=False, error=f"checkout not found at {entry.path}")
    if not gitio.is_git_repo(entry.path):
        return RepoStatus(present=True, is_git=False, error="not a git repository")
    if entry.subdir and not entry.root.is_dir():
        return RepoStatus(present=True, is_git=True, error=f"subdir {entry.subdir!r} not found")
    try:
        branch = gitio.current_branch(entry.path)
        return RepoStatus(
            present=True,
            is_git=True,
            branch=branch,
            on_default_branch=branch == entry.default_branch,
            dirty=gitio.is_dirty(entry.path),
            head_sha=gitio.head_sha(entry.path),
        )
    except RepoAccessError as exc:
        return RepoStatus(present=True, is_git=True, error=str(exc))


def _load_metrics(repo: Path) -> tuple[Mapping[str, Any] | None, str]:
    path = repo / GENERATED_DIR / "metrics.json"
    if not path.is_file():
        return None, "no metrics.json — kit not applied or diagrams never generated"
    try:
        metrics = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"unreadable metrics.json: {exc}"
    if not isinstance(metrics, dict):
        return None, "metrics.json is not a JSON object"
    for section in ("graph", "size", "complexity", "tests", "measurement", "review", "quality"):
        if section in metrics and not isinstance(metrics[section], dict):
            return None, f"invalid metrics section: {section}"
    return metrics, ""


def _load_architecture(repo: Path) -> Mapping[str, Any]:
    path = repo / ARCHITECTURE_TOML
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _tier_of(component: str, architecture: Mapping[str, Any]) -> str:
    tiers = architecture.get("tiers", {})
    if isinstance(tiers, Mapping):
        for tier, members in tiers.items():
            if isinstance(members, list) and component in members:
                return str(tier)
    return ""


def component_rows(
    metrics: Mapping[str, Any], architecture: Mapping[str, Any]
) -> tuple[ComponentRow, ...]:
    """Merge the snapshot's per-component graph/size/complexity stats into rows."""
    graph = metrics.get("graph", {}).get("components", {})
    size = metrics.get("size", {}).get("components", {})
    complexity = metrics.get("complexity", {}).get("components", {})
    if not isinstance(graph, Mapping):
        return ()
    rows: list[ComponentRow] = []
    for name in sorted(graph):
        g = graph.get(name, {})
        s = size.get(name, {}) if isinstance(size, Mapping) else {}
        c = complexity.get(name, {}) if isinstance(complexity, Mapping) else {}
        rows.append(
            ComponentRow(
                name=name,
                tier=_tier_of(name, architecture),
                modules=int(s.get("modules", 0)),
                lines=int(s.get("lines", 0)),
                sloc=int(s.get("sloc", 0)),
                fan_in=int(g.get("fan_in", 0)),
                fan_out=int(g.get("fan_out", 0)),
                instability=g.get("instability"),
                max_complexity=int(c.get("max_complexity", 0)),
                functions_over_10=int(c.get("functions_over_10", 0)),
            )
        )
    return tuple(rows)


def _doc_listing(repo: Path) -> tuple[str, ...]:
    generated = repo / GENERATED_DIR
    if not generated.is_dir():
        return ()
    return tuple(
        sorted(str(p.relative_to(generated)) for p in generated.rglob("*.md") if p.is_file())
    )


def measurement_status(entry: RepoEntry, metrics: Mapping[str, Any] | None) -> tuple[str, str]:
    if metrics is None:
        return "missing", "No metrics snapshot"
    meta = metrics.get("measurement", {})
    if not meta:
        return "legacy", "No provenance; measurement scope and comparability are unverified"
    try:
        root = entry.root.resolve()
        source = (root / meta["scope"]["source_root"]).resolve()
        if not source.is_relative_to(root) or not source.is_dir():
            return "invalid", "Measurement source root is unavailable or outside the checkout"
        suffixes = meta["scope"]["source_suffixes"]
        sources = {}
        for path in sorted(source.rglob("*")):
            if path.is_file() and path.suffix in suffixes:
                if not path.resolve().is_relative_to(root):
                    return "invalid", "Source file escapes the checkout"
                sources[path.relative_to(root).as_posix()] = hashlib.sha256(
                    path.read_text(encoding="utf-8").encode()
                ).hexdigest()
        current = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
        if current != meta["source_hash"]:
            return "stale", "Source changed since this snapshot; regenerate metrics"
        return "measured", "Snapshot matches the measured source files"
    except (KeyError, TypeError, OSError, AttributeError, ValueError) as exc:
        return "invalid", f"Invalid measurement provenance: {exc}"


def scan(entry: RepoEntry) -> RepoSnapshot:
    """Read one registered checkout into a RepoSnapshot (never raises)."""
    status = _status(entry)
    if not status.present or not status.is_git or status.error:
        return RepoSnapshot(entry=entry, status=status)

    metrics, metrics_error = _load_metrics(entry.root)
    if metrics_error:
        status = RepoStatus(
            present=True,
            is_git=True,
            branch=status.branch,
            on_default_branch=status.on_default_branch,
            dirty=status.dirty,
            head_sha=status.head_sha,
            error=metrics_error,
        )
    architecture = _load_architecture(entry.root)
    schema = metrics.get("schema") if metrics else None
    try:
        budgets = budget_statuses(architecture, metrics)
        components = component_rows(metrics, architecture) if metrics else ()
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        return RepoSnapshot(
            entry=entry,
            status=replace(status, error=f"Invalid metrics/configuration: {exc}"),
            measurement_status="invalid",
        )
    freshness, note = measurement_status(entry, metrics)
    return RepoSnapshot(
        entry=entry,
        status=status,
        schema=schema if isinstance(schema, int) else None,
        metrics=metrics,
        budgets=budgets,
        components=components,
        docs=_doc_listing(entry.root),
        measurement_status=freshness,
        measurement_note=note,
        architecture=architecture,
    )
