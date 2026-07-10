"""The hub's own kit application must be byte-identical to the canonical kit.

Every port may legitimately reformat or regenericize (drift compares ASTs), but
the hub IS the canon's home — if its dogfooded copy differed even by a byte,
the canon would have forked silently. Byte equality, no normalization.
"""

from __future__ import annotations

from pathlib import Path

from guardrail_hub.kit import kit_root, kit_version, load_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_dogfooded_core_files_are_byte_identical() -> None:
    unequal: list[str] = []
    for kit_file in load_manifest():
        if kit_file.role.startswith("adapter-"):
            continue  # hub enables no adapters
        applied = REPO_ROOT / kit_file.path
        if (kit_root() / kit_file.path).read_bytes() != applied.read_bytes():
            unequal.append(kit_file.path)

    assert not unequal, (
        f"hub's kit application differs from kit/ canon: {unequal}. "
        "Edit kit/ (the canon) and re-apply, never the applied copy."
    )


def test_dogfooded_kit_version_is_current() -> None:
    installed = (REPO_ROOT / "tests" / "guardrail" / "KIT_VERSION").read_text(encoding="utf-8")

    assert installed.strip() == kit_version()
