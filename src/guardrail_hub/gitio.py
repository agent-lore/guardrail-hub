"""The one git subprocess seam.

Every git invocation in the hub goes through these helpers so the rest of the
code never touches ``subprocess`` and tests can exercise real git against tiny
fixture repos. All helpers run with ``cwd=repo`` and raise ``RepoAccessError``
on failure.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from guardrail_hub.errors import RepoAccessError

_TIMEOUT_S = 30


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
            timeout=_TIMEOUT_S,
        )
    except FileNotFoundError as exc:  # git itself missing
        raise RepoAccessError("git executable not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise RepoAccessError(f"git {' '.join(args)} timed out in {repo}") from exc
    except subprocess.CalledProcessError as exc:
        raise RepoAccessError(
            f"git {' '.join(args)} failed in {repo}: {exc.stderr.strip() or exc.stdout.strip()}"
        ) from exc
    return result.stdout


def is_git_repo(repo: Path) -> bool:
    try:
        return _git(repo, "rev-parse", "--is-inside-work-tree").strip() == "true"
    except RepoAccessError:
        return False


def head_sha(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def current_branch(repo: Path) -> str:
    """Current branch name, or the literal ``HEAD`` when detached."""
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()


def branch_tip(repo: Path, branch: str) -> str | None:
    """Sha of a local branch tip, or None when the branch does not exist."""
    try:
        return _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").strip()
    except RepoAccessError:
        return None


def is_dirty(repo: Path) -> bool:
    return bool(_git(repo, "status", "--porcelain").strip())


def first_parent_log(repo: Path, ref: str, path: str) -> list[tuple[str, str]]:
    """``(short sha, YYYY-MM-DD)`` per first-parent commit of ``ref`` touching ``path``.

    Oldest first — one point per mainline commit, so merge side-branch churn
    never appears in the series.
    """
    out = _git(repo, "log", "--reverse", "--first-parent", "--format=%h %cs", ref, "--", path)
    pairs: list[tuple[str, str]] = []
    for line in out.splitlines():
        sha, _, date = line.partition(" ")
        if sha and date:
            pairs.append((sha, date))
    return pairs


def show_file(repo: Path, sha: str, path: str) -> str:
    """File content at a commit. Raises RepoAccessError when absent at that commit."""
    return _git(repo, "show", f"{sha}:{path}")
