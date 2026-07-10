"""Access to the canonical kit: its files, manifest, and version.

The kit lives at the top-level ``kit/`` directory of the checkout so pytest,
ruff, and pyright never treat its templates as hub code; a wheel install falls
back to the copy hatch force-includes as ``guardrail_hub/_kit``.
"""

from __future__ import annotations

import pathlib
import tomllib
from dataclasses import dataclass

from guardrail_hub.errors import KitError


def kit_root() -> pathlib.Path:
    """The canonical kit directory (checkout first, packaged copy as fallback)."""
    checkout = pathlib.Path(__file__).resolve().parents[2] / "kit"
    if checkout.is_dir():
        return checkout
    packaged = pathlib.Path(__file__).resolve().parent / "_kit"
    if packaged.is_dir():
        return packaged
    raise KitError("canonical kit directory not found (checkout kit/ or packaged _kit/)")


def kit_version() -> str:
    try:
        return (kit_root() / "VERSION").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise KitError(f"cannot read kit VERSION: {exc}") from exc


@dataclass(frozen=True)
class KitFile:
    """One manifest entry: where the file lives in a target repo, and its role."""

    path: str
    role: str
    alt_paths: tuple[str, ...] = ()

    @property
    def is_python(self) -> bool:
        return self.path.endswith(".py")

    def source(self) -> str:
        """Canonical content of this kit file."""
        try:
            return (kit_root() / self.path).read_text(encoding="utf-8")
        except OSError as exc:
            raise KitError(f"kit file missing from canonical kit: {self.path}") from exc


def load_manifest() -> tuple[KitFile, ...]:
    """Parse and validate kit/manifest.toml."""
    path = kit_root() / "manifest.toml"
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise KitError(f"cannot read kit manifest: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise KitError(f"invalid kit manifest: {exc}") from exc
    files = raw.get("files")
    if not isinstance(files, list) or not files:
        raise KitError("kit manifest has no [[files]] entries")
    entries: list[KitFile] = []
    for table in files:
        if not isinstance(table, dict) or "path" not in table or "role" not in table:
            raise KitError(f"malformed manifest entry: {table!r}")
        entries.append(
            KitFile(
                path=str(table["path"]),
                role=str(table["role"]),
                alt_paths=tuple(str(p) for p in table.get("alt_paths", [])),
            )
        )
    return tuple(entries)
