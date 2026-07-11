"""File-path → module → component mapping from a repo's architecture.toml.

Hub-side counterpart of the kit's ``tests/guardrail/_common.py`` mapping —
that code is per-repo test tooling, not an importable library. Paths here are
git paths: relative to the checkout root, POSIX separators, so ``entry.subdir``
is part of the source prefix for monorepo entries.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass

from guardrail_hub.models import RepoEntry

_CPP_SUFFIXES = (".h", ".hpp", ".c", ".cc", ".cpp")


@dataclass(frozen=True)
class ArchMap:
    """Module naming + component ownership rules of one kit instance."""

    root_package: str
    language: str
    src_prefix: str  # checkout-relative POSIX prefix of the source root
    components: dict[str, tuple[str, ...]]

    def module_of(self, path: str) -> str | None:
        """Dotted module name for a checkout-relative source path, else None.

        Python: one ``.py`` file per module (``__init__`` names the package).
        C++: header/impl pairs merge by stem, mirroring the kit.
        """
        if not path.startswith(self.src_prefix + "/"):
            return None
        rel = path[len(self.src_prefix) + 1 :]
        stem, _, suffix = rel.rpartition(".")
        if self.language == "cpp":
            if "." + suffix not in _CPP_SUFFIXES:
                return None
        elif suffix != "py":
            return None
        parts = [self.root_package, *stem.split("/")]
        if parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)

    def component_of(self, module: str) -> str | None:
        """Owning component by longest matching module-prefix (kit semantics)."""
        best, best_len = None, -1
        for comp, prefixes in self.components.items():
            for pfx in prefixes:
                if (module == pfx or module.startswith(pfx + ".")) and len(pfx) > best_len:
                    best, best_len = comp, len(pfx)
        return best


def load_archmap(entry: RepoEntry) -> ArchMap | None:
    """ArchMap for a registered repo, or None when its config is absent/invalid."""
    try:
        raw = (entry.root / "docs" / "architecture.toml").read_text(encoding="utf-8")
        arch = tomllib.loads(raw)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    project = arch.get("project", {})
    root_package = project.get("root_package")
    components = arch.get("components")
    if not isinstance(root_package, str) or not isinstance(components, dict):
        return None
    language = project.get("language", "python")
    src_layout = project.get("src_layout", "src")
    parts = [p for p in (entry.subdir, src_layout) if p]
    if language != "cpp":
        parts.append(root_package)
    return ArchMap(
        root_package=root_package,
        language=language,
        src_prefix="/".join(parts),
        components={
            comp: tuple(p for p in prefixes if isinstance(p, str))
            for comp, prefixes in components.items()
            if isinstance(prefixes, list)
        },
    )
