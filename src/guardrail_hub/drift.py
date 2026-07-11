"""Kit drift: normalized comparison of a repo's kit copy against the canonical kit.

Comparison is AST-based so formatting (the repos disagree on ruff line-length),
comments (pyright-ignore annotations), and docstrings (genericized per repo)
all vanish — what remains is real logic drift. Adapter files are only expected
where the repo's ``docs/architecture.toml`` enables the adapter, and files the
repo added on top of the kit report as informational ``extra``.
"""

from __future__ import annotations

import ast
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from guardrail_hub.kit import KitFile, kit_version, load_manifest
from guardrail_hub.models import DriftReport, FileDrift, RepoEntry

VERSION_FILE = "tests/guardrail/KIT_VERSION"
PRE_HUB = "pre-hub"

# Files legitimately present in a kit application that are not manifest content.
_KNOWN_COMPANIONS = {"KIT_VERSION", "__init__.py"}


def _strip_docstrings(tree: ast.Module) -> None:
    """Remove docstring statements from the module and every class/function."""
    nodes: list[ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef] = [tree]
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            nodes.append(node)
    for node in nodes:
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            del body[0]
            if not body:
                body.append(ast.Pass())


def _string_parts(node: ast.expr) -> list[ast.expr] | None:
    """The concatenable pieces of a string-ish node (None = not a string)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node]
    if isinstance(node, ast.JoinedStr):
        return list(node.values)
    return None


class _FoldStringConcat(ast.NodeTransformer):
    """Fold ``"a" + "b"`` (and the f-string equivalents) into one literal.

    Splitting a long literal into explicitly ``+``-joined parts is how the
    88-width ports satisfy both their line length and GitHub code quality's
    implicit-concatenation-in-a-list rule — a formatting decision, not logic
    drift, so normalize it away like whitespace. Adjacent constant pieces are
    merged exactly the way CPython's parser merges implicitly concatenated
    literals, so the fold reproduces the canonical AST byte-for-byte.
    """

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        self.generic_visit(node)
        if not isinstance(node.op, ast.Add):
            return node
        left, right = _string_parts(node.left), _string_parts(node.right)
        if left is None or right is None:
            return node
        merged: list[ast.expr] = []
        for part in left + right:
            if (
                merged
                and isinstance(part, ast.Constant)
                and isinstance(part.value, str)
                and isinstance(merged[-1], ast.Constant)
                and isinstance(merged[-1].value, str)
            ):
                merged[-1] = ast.copy_location(ast.Constant(merged[-1].value + part.value), part)
            else:
                merged.append(part)
        if len(merged) == 1 and isinstance(merged[0], ast.Constant):
            return ast.copy_location(merged[0], node)
        return ast.copy_location(ast.JoinedStr(values=merged), node)


def normalized_ast_dump(source: str) -> str:
    """Formatting-, comment-, and docstring-immune fingerprint of Python source."""
    tree = ast.parse(source)
    _strip_docstrings(tree)
    tree = _FoldStringConcat().visit(tree)
    return ast.dump(tree, include_attributes=False)


def _adapters_enabled(architecture: Mapping[str, Any]) -> set[str]:
    enabled = set()
    if architecture.get("tool_catalog", {}).get("include_modules"):
        enabled.add("adapter-tool-catalog")
    if architecture.get("containers", {}).get("stores"):
        enabled.add("adapter-containers")
    if architecture.get("project", {}).get("language") == "cpp":
        enabled.add("adapter-cpp")
    return enabled


def _load_architecture(repo: Path) -> Mapping[str, Any]:
    path = repo / "docs" / "architecture.toml"
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _resolve(repo: Path, kit_file: KitFile) -> Path | None:
    for candidate in (kit_file.path, *kit_file.alt_paths):
        if (repo / candidate).is_file():
            return repo / candidate
    return None


def _compare_python(kit_file: KitFile, found: Path) -> FileDrift:
    try:
        canonical = normalized_ast_dump(kit_file.source())
        actual = normalized_ast_dump(found.read_text(encoding="utf-8"))
    except (SyntaxError, OSError) as exc:
        return FileDrift(path=kit_file.path, role=kit_file.role, status="error", detail=str(exc))
    if canonical == actual:
        return FileDrift(path=kit_file.path, role=kit_file.role, status="same")
    return FileDrift(
        path=kit_file.path,
        role=kit_file.role,
        status="differs",
        detail="logic differs from canonical (formatting/docstrings already ignored)",
    )


def _extras(repo: Path, manifest: tuple[KitFile, ...]) -> list[FileDrift]:
    guardrail = repo / "tests" / "guardrail"
    if not guardrail.is_dir():
        return []
    known = {Path(f.path).name for f in manifest} | {
        Path(alt).name for f in manifest for alt in f.alt_paths
    }
    extras: list[FileDrift] = []
    for path in sorted(guardrail.iterdir()):
        if not path.is_file() or path.name in known or path.name in _KNOWN_COMPANIONS:
            continue
        if path.suffix not in (".py", ".md"):
            continue
        extras.append(
            FileDrift(
                path=f"tests/guardrail/{path.name}",
                role="repo-local",
                status="extra",
                detail="repo-local addition on top of the kit (fine)",
            )
        )
    return extras


def installed_version(repo: Path) -> str:
    path = repo / VERSION_FILE
    if not path.is_file():
        return PRE_HUB
    try:
        return path.read_text(encoding="utf-8").strip() or PRE_HUB
    except OSError:
        return PRE_HUB


def compare_repo(entry: RepoEntry) -> DriftReport:
    """Drift report for one registered repo (never raises for repo-side problems)."""
    manifest = load_manifest()
    architecture = _load_architecture(entry.root)
    enabled = _adapters_enabled(architecture)

    results: list[FileDrift] = []
    for kit_file in manifest:
        adapter = kit_file.role.startswith("adapter-")
        found = _resolve(entry.root, kit_file)
        if adapter and kit_file.role not in enabled:
            if found is not None:
                results.append(
                    FileDrift(
                        path=kit_file.path,
                        role=kit_file.role,
                        status="extra",
                        detail="adapter file present but its config section is not populated",
                    )
                )
            continue  # adapter not enabled and file absent: not expected, not reported
        if found is None:
            results.append(FileDrift(path=kit_file.path, role=kit_file.role, status="missing"))
            continue
        if kit_file.is_python:
            results.append(_compare_python(kit_file, found))
        else:
            results.append(
                FileDrift(
                    path=kit_file.path, role=kit_file.role, status="same", detail="presence only"
                )
            )
    results.extend(_extras(entry.root, manifest))
    return DriftReport(
        repo=entry.name,
        kit_version=kit_version(),
        installed_version=installed_version(entry.root),
        files=tuple(results),
    )
