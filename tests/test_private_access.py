"""Unit tests for the kit's cross-module private-access scanner (seams metrics)."""

from __future__ import annotations

import ast
import textwrap
from collections import Counter

import pytest
from tests.guardrail import _private_access as pa

INTERNAL = {"pkg", "pkg.a", "pkg.b", "pkg._util", "pkg.server", "pkg.sub", "pkg.sub.deep"}


def scan(source: str, module: str | None = "pkg.a", **kwargs) -> Counter[str]:
    return pa.scan_tree(ast.parse(textwrap.dedent(source)), INTERNAL, module=module, **kwargs)


# --- L1: private imports ---------------------------------------------------- #
def test_private_symbol_import_counts() -> None:
    hits = scan("from pkg.b import _helper\n")

    assert hits == Counter({"pkg.b._helper": 1})


def test_public_symbol_import_does_not_count() -> None:
    assert scan("from pkg.b import helper\n") == Counter()


def test_private_import_from_own_module_is_exempt() -> None:
    assert scan("from pkg.b import _helper\n", module="pkg.b") == Counter()


def test_relative_private_import_resolves() -> None:
    hits = scan("from ._util import _secret\n")

    assert hits == Counter({"pkg._util._secret": 1})


def test_private_module_import_counts() -> None:
    hits = scan("from pkg import _util\n")

    assert hits == Counter({"pkg._util": 1})


def test_relative_import_in_package_init_resolves_to_own_package() -> None:
    hits = scan("from . import _util\n", module="pkg", is_package=True)

    assert hits == Counter({"pkg._util": 1})


def test_dunder_import_does_not_count() -> None:
    assert scan("from pkg.b import __version__\n") == Counter()


def test_external_imports_are_ignored() -> None:
    assert scan("from os.path import _joinrealpath\n") == Counter()


# --- L2: module-attribute access -------------------------------------------- #
def test_module_attr_via_from_import() -> None:
    hits = scan("from pkg import b\nb._helper()\nb._helper()\n")

    assert hits == Counter({"pkg.b._helper": 2})


def test_module_attr_via_plain_import_chain() -> None:
    hits = scan("import pkg.b\npkg.b._helper()\n")

    assert hits == Counter({"pkg.b._helper": 1})


def test_module_attr_via_alias() -> None:
    hits = scan("import pkg.b as bee\nbee._helper()\n")

    assert hits == Counter({"pkg.b._helper": 1})


def test_module_attr_on_own_module_is_exempt() -> None:
    assert scan("import pkg.b\npkg.b._helper()\n", module="pkg.b") == Counter()


def test_public_module_attr_does_not_count() -> None:
    assert scan("from pkg import b\nb.helper()\n") == Counter()


def test_dunder_module_attr_does_not_count() -> None:
    assert scan("from pkg import b\nprint(b.__doc__)\n") == Counter()


def test_external_module_attr_is_ignored() -> None:
    assert scan("import os\nos._exit(1)\n") == Counter()


# --- L3: annotated-instance access ------------------------------------------ #
def test_annotated_param_private_attr_counts() -> None:
    hits = scan(
        """
        from pkg.server import Server

        def go(server: Server) -> None:
            server._emit("x")
        """
    )

    assert hits == Counter({"pkg.server.Server._emit": 1})


def test_type_checking_import_and_string_annotation() -> None:
    hits = scan(
        """
        from typing import TYPE_CHECKING

        if TYPE_CHECKING:
            from pkg.server import Server

        def go(server: "Server") -> None:
            server._emit("x")
        """
    )

    assert hits == Counter({"pkg.server.Server._emit": 1})


def test_optional_annotation_still_binds() -> None:
    hits = scan(
        """
        from pkg.server import Server

        def go(server: Server | None) -> None:
            server._config
        """
    )

    assert hits == Counter({"pkg.server.Server._config": 1})


def test_annassign_variable_binds() -> None:
    hits = scan(
        """
        from pkg.server import Server

        srv: Server = make()
        srv._emit("x")
        """
    )

    assert hits == Counter({"pkg.server.Server._emit": 1})


def test_container_annotation_does_not_bind() -> None:
    hits = scan(
        """
        from pkg.server import Server

        def go(servers: list[Server]) -> None:
            servers._x
        """
    )

    assert hits == Counter()


def test_self_access_never_counts() -> None:
    hits = scan(
        """
        from pkg.server import Server

        class Own(Server):
            def go(self) -> None:
                self._emit("x")
        """
    )

    assert hits == Counter()


def test_ambiguous_union_across_modules_does_not_bind() -> None:
    hits = scan(
        """
        from pkg.server import Server
        from pkg.b import Client

        def go(x: Server | Client) -> None:
            x._emit("x")
        """
    )

    assert hits == Counter()


def test_annotations_flag_disables_l3_but_not_imports() -> None:
    hits = scan(
        """
        from pkg.b import _helper
        from pkg.server import Server

        def go(server: Server) -> None:
            server._emit("x")
        """,
        annotations=False,
    )

    assert hits == Counter({"pkg.b._helper": 1})


def test_annotated_class_from_own_module_is_exempt() -> None:
    hits = scan(
        """
        from pkg.server import Server

        def go(server: Server) -> None:
            server._emit("x")
        """,
        module="pkg.server",
    )

    assert hits == Counter()


# --- detail formatting / discovery / dispatch -------------------------------- #
def test_details_sorted_by_count_then_text_with_multiplier() -> None:
    pairs = Counter({("m.a", "m.b._x"): 2, ("m.a", "m.b._a"): 1, ("m.c", "m.b._x"): 2})

    assert pa._details(pairs) == [
        "m.a -> m.b._x (x2)",
        "m.c -> m.b._x (x2)",
        "m.a -> m.b._a",
    ]


def test_details_are_capped() -> None:
    pairs = Counter({(f"m.src{i:03d}", "m.b._x"): 1 for i in range(pa.DETAIL_CAP + 5)})

    assert len(pa._details(pairs)) == pa.DETAIL_CAP


def test_test_file_discovery_excludes_guardrail_kit() -> None:
    files = pa._test_files()

    assert files, "hub's own tests/ should be discovered"
    assert not [f for f in files if "guardrail" in f.parts]


def test_seams_metrics_zeroed_for_non_python(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pa, "LANGUAGE", "cpp")

    assert pa.seams_metrics() == {
        "cross_module_private_refs": 0,
        "cross_module_private_detail": [],
        "tests_private_imports": 0,
        "tests_private_detail": [],
    }
