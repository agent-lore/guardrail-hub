"""Hub-managed clones of the registered repos.

When ``[checkouts]`` is enabled the dashboard reads committed default-branch
state from clones the hub owns, so a working copy sitting dirty on a feature
branch never distorts the views. ``resolve`` rewrites a registry entry's
``path`` to the clone location — everything downstream (scanners, miners, doc
routes) follows that path. ``refresh`` creates or updates a clone from the
working copy's ``origin`` remote.

Clones are keyed by the registered checkout *path*, so a monorepo's several
subdir entries share one clone. Failures degrade to returned messages (badges,
never exceptions — repo-side problems must render, not 500).
"""

from __future__ import annotations

import datetime
import hashlib
import re
import shutil
from dataclasses import replace
from pathlib import Path

from guardrail_hub import gitio
from guardrail_hub.config import CheckoutsConfig
from guardrail_hub.errors import RepoAccessError
from guardrail_hub.models import RepoEntry


def clone_dir(base: Path, checkout: Path) -> Path:
    """Clone location for a registered checkout: readable slug + path hash.

    The hash keys the clone to the full registered path, so two checkouts with
    the same basename get distinct clones while a monorepo's subdir entries
    (same path) share one.
    """
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", checkout.name) or "repo"
    digest = hashlib.sha256(str(checkout).encode("utf-8")).hexdigest()[:8]
    return base / f"{slug}-{digest}"


def resolve(cfg: CheckoutsConfig, entry: RepoEntry) -> RepoEntry:
    """The registry entry the readers should use: unchanged, or clone-pathed."""
    if not cfg.enabled:
        return entry
    return replace(entry, path=clone_dir(cfg.dir, entry.path))


def refresh(cfg: CheckoutsConfig, entry: RepoEntry) -> str:
    """Create or update the managed clone for ``entry`` (the *registry* entry).

    Returns "" on success (or when disabled), else an error message. The clone
    URL is discovered from the working copy's ``origin`` each time, so a
    re-pointed remote is followed. An interrupted clone (dest exists but is
    not a git repo) is discarded and re-cloned.
    """
    if not cfg.enabled:
        return ""
    dest = clone_dir(cfg.dir, entry.path)
    branch = entry.default_branch
    try:
        url = gitio.remote_url(entry.path)
    except RepoAccessError as exc:
        return f"cannot discover origin of {entry.path}: {exc}"
    try:
        if not gitio.is_git_repo(dest):
            if dest.exists():
                shutil.rmtree(dest)
            gitio.clone(url, dest, branch)
        elif gitio.remote_url(dest) != url:
            gitio.set_remote_url(dest, url)
        gitio.fetch(dest, branch)
        gitio.checkout_reset(dest, branch)
    except (RepoAccessError, OSError) as exc:
        return str(exc)
    return ""


def last_fetch(clone: Path) -> str:
    """When the clone last talked to its origin, from git bookkeeping mtimes.

    ``FETCH_HEAD`` is stamped by every fetch; a freshly cloned repo that never
    fetched falls back to ``HEAD``. Empty string when neither exists (no clone
    yet).
    """
    for name in ("FETCH_HEAD", "HEAD"):
        try:
            mtime = (clone / ".git" / name).stat().st_mtime
        except OSError:
            continue
        stamp = datetime.datetime.fromtimestamp(mtime, tz=datetime.UTC)
        return stamp.strftime("%Y-%m-%d %H:%M UTC")
    return ""
