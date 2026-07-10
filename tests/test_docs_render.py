"""Unit tests for markdown rendering, mermaid fences, and link rewriting."""

from __future__ import annotations

from pathlib import Path

from guardrail_hub.docs_render import contained, render_markdown, render_plain, rewrite_href


def test_mermaid_fence_becomes_pre() -> None:
    html = render_markdown("```mermaid\ngraph TD\n  A --> B\n```\n", "r", "architecture.md")

    assert '<pre class="mermaid">graph TD\n  A --&gt; B\n</pre>' in html
    assert "<code" not in html


def test_normal_fence_unchanged() -> None:
    html = render_markdown("```sh\nmake diagrams\n```\n", "r", "README.md")

    assert "<code" in html and "mermaid" not in html


def test_table_renders() -> None:
    html = render_markdown("| a | b |\n|---|---|\n| 1 | 2 |\n", "r", "metrics.md")

    assert "<table>" in html


def test_links_within_generated_untouched() -> None:
    html = render_markdown("[arch](architecture.md) [c](components/Core.md)", "r", "README.md")

    assert 'href="architecture.md"' in html
    assert 'href="components/Core.md"' in html


def test_relative_up_link_within_generated_untouched() -> None:
    # from components/Core.md back to the index
    assert rewrite_href("../README.md", "r", "components/Core.md") == "../README.md"


def test_escaping_link_rewritten_to_file_route() -> None:
    assert (
        rewrite_href("../architecture.toml", "r", "README.md")
        == "/repos/r/file/docs/architecture.toml"
    )
    assert (
        rewrite_href("../../adr/0006-x.md", "r", "components/Core.md")
        == "/repos/r/file/docs/adr/0006-x.md"
    )
    assert rewrite_href("../../CONTEXT.md", "r", "README.md") == "/repos/r/file/CONTEXT.md"


def test_escaping_link_with_fragment() -> None:
    assert (
        rewrite_href("../../CONTEXT.md#vocab", "r", "README.md") == "/repos/r/file/CONTEXT.md#vocab"
    )


def test_external_and_anchor_links_untouched() -> None:
    for href in ("https://example.com", "mailto:x@y.z", "#section", "/absolute"):
        assert rewrite_href(href, "r", "README.md") == href


def test_link_escaping_repo_root_left_alone() -> None:
    assert rewrite_href("../../../../etc/passwd", "r", "README.md") == "../../../../etc/passwd"


def test_render_plain_escapes() -> None:
    assert render_plain("<script>") == '<pre class="plain-file">&lt;script&gt;</pre>'


def test_contained_accepts_inside(tmp_path: Path) -> None:
    inside = tmp_path / "docs" / "x.md"
    inside.parent.mkdir()
    inside.write_text("x", encoding="utf-8")

    assert contained(tmp_path, tmp_path / "docs" / "x.md") == inside.resolve()


def test_contained_rejects_traversal_and_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("s", encoding="utf-8")
    link = root / "leak"
    link.symlink_to(tmp_path / "secret.txt")

    assert contained(root, root / ".." / "secret.txt") is None
    assert contained(root, link) is None
