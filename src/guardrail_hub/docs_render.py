"""Markdown rendering for the generated docs.

Pages are served at ``/repos/{name}/docs/{path}``, a URL space that mirrors
``docs/generated/`` on disk — so relative links between generated pages (and
the Mermaid ``click … "components/X.md"`` directives inside fences, which no
markdown pass can touch) resolve with no rewriting. Only links that ESCAPE
``docs/generated`` (ADRs, ``../architecture.toml``, ``CONTEXT.md``) are
rewritten to the ``/repos/{name}/file/…`` route.
"""

from __future__ import annotations

import html
import posixpath
from collections.abc import Sequence
from pathlib import Path

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict

GENERATED_PREFIX = "docs/generated"


class _HubRenderer(RendererHTML):
    """markdown-it renderer with mermaid fences and escape-link rewriting."""

    def fence(self, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType) -> str:
        token = tokens[idx]
        if token.info.strip().split(" ")[0] == "mermaid":
            return f'<pre class="mermaid">{html.escape(token.content)}</pre>\n'
        return super().fence(tokens, idx, options, env)


def _is_external(href: str) -> bool:
    return href.startswith(("http://", "https://", "mailto:", "#", "/"))


def rewrite_href(href: str, repo_name: str, doc_rel: str) -> str:
    """Rewrite a link that escapes docs/generated to the /file/ route.

    ``doc_rel`` is the current page's path relative to docs/generated. Links
    that stay inside docs/generated are returned unchanged (the URL space
    mirrors the tree). Links that escape the repo root entirely are left
    alone — serving containment turns them into 404s.
    """
    if _is_external(href):
        return href
    target, _, fragment = href.partition("#")
    if not target:
        return href
    within_generated = posixpath.normpath(posixpath.join(posixpath.dirname(doc_rel), target))
    if not within_generated.startswith(".."):
        return href
    repo_rel = posixpath.normpath(posixpath.join(GENERATED_PREFIX, within_generated))
    if repo_rel.startswith(".."):
        return href
    suffix = f"#{fragment}" if fragment else ""
    return f"/repos/{repo_name}/file/{repo_rel}{suffix}"


def _make_parser(repo_name: str, doc_rel: str) -> MarkdownIt:
    md = MarkdownIt("commonmark", renderer_cls=_HubRenderer).enable("table")

    def _rewrite_links(state) -> None:
        for token in state.tokens:
            for child in token.children or []:
                if child.type == "link_open":
                    href = child.attrGet("href")
                    if href:
                        child.attrSet("href", rewrite_href(href, repo_name, doc_rel))

    md.core.ruler.push("hub_rewrite_links", _rewrite_links)
    return md


def render_markdown(text: str, repo_name: str, doc_rel: str) -> str:
    """Render one generated doc to an HTML fragment."""
    return _make_parser(repo_name, doc_rel).render(text)


def render_plain(text: str) -> str:
    """Non-markdown file bodies: escaped preformatted text."""
    return f'<pre class="plain-file">{html.escape(text)}</pre>'


def contained(root: Path, candidate: Path) -> Path | None:
    """Resolve ``candidate`` and require it inside ``root`` (symlink-safe)."""
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return resolved
