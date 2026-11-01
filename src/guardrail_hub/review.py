"""Revision comparisons over committed snapshots and their accepted policies."""

from __future__ import annotations

import functools
import hashlib
import importlib.util
import json
import tomllib
from pathlib import PurePosixPath
from types import ModuleType
from typing import Any

from guardrail_hub import gitio
from guardrail_hub.budgets import budget_statuses
from guardrail_hub.errors import RepoAccessError
from guardrail_hub.history import snapshot_path
from guardrail_hub.kit import kit_root
from guardrail_hub.models import RepoEntry


@functools.lru_cache(maxsize=1)
def engine() -> ModuleType:
    """Use the shipped canonical engine; never execute code from monitored repos."""
    spec = importlib.util.spec_from_file_location(
        "guardrail_quality", kit_root() / "scripts/quality.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("canonical quality engine unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_source(entry: RepoEntry, sha: str, snapshot: dict) -> None:
    meta = snapshot.get("measurement", {})
    if not meta.get("source_hash"):
        return  # old snapshots carry no verifiable source provenance
    scope = meta["scope"]
    source = PurePosixPath(scope["source_root"])
    if source.is_absolute() or ".." in source.parts:
        raise ValueError("invalid measurement source root")
    prefix = f"{entry.subdir}/" if entry.subdir else ""
    files = gitio.tree_files(entry.path, sha, prefix + str(source))
    sources = {
        path[len(prefix) :]: hashlib.sha256(
            gitio.show_file(entry.path, sha, path).encode()
        ).hexdigest()
        for path in files
        if PurePosixPath(path).suffix in scope["source_suffixes"]
    }
    actual = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
    if actual != meta["source_hash"]:
        raise ValueError("metrics are stale for this revision; source hash does not match")


def read_revision(entry: RepoEntry, revision: str) -> dict[str, Any]:
    sha = gitio.resolve_commit(entry.path, revision)
    try:
        snapshot = json.loads(gitio.show_file(entry.path, sha, snapshot_path(entry)))
        if not isinstance(snapshot, dict):
            raise ValueError("metrics snapshot is not an object")
        verify_source(entry, sha, snapshot)
        root = f"{entry.subdir}/" if entry.subdir else ""
        arch = tomllib.loads(gitio.show_file(entry.path, sha, root + "docs/architecture.toml"))
        config = engine().policy_config(arch)
        # Actuals are measured at this revision; policy is always read from the
        # same commit, even when only architecture.toml changed.
        config["actuals"] = {b.key: b.actual for b in budget_statuses(arch, snapshot)}
        snapshot["review"] = config
        return {"sha": sha, "metrics": snapshot}
    except (ValueError, TypeError, AttributeError) as exc:
        raise RepoAccessError(f"unreadable measurement at {sha[:12]}: {exc}") from exc


def compare(entry: RepoEntry, base: str, head: str) -> dict[str, Any]:
    try:
        before, after = read_revision(entry, base), read_revision(entry, head)
        errors = engine().policy_errors(after["metrics"].get("review", {}))
        if errors:
            raise ValueError("; ".join(errors))
        result = engine().report(before["metrics"], after["metrics"])
        return {**result, "base": before["sha"], "head": after["sha"], "error": ""}
    except (RepoAccessError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return {"base": base, "head": head, "error": str(exc)}


def current_report(metrics: dict, architecture: dict | None = None) -> dict:
    """Current violations/evidence without claiming a historical improvement."""
    try:
        if architecture is not None:
            metrics = dict(metrics)
            config = engine().policy_config(architecture)
            config["actuals"] = {b.key: b.actual for b in budget_statuses(architecture, metrics)}
            metrics["review"] = config
        errors = engine().policy_errors(metrics.get("review", {}))
        if errors:
            raise ValueError("; ".join(errors))
        return engine().report(metrics, metrics)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return {"error": str(exc), "violations": [], "suggestions": [], "quality": {}}
