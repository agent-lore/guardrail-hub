"""Unit tests for config discovery, parsing, and env overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from guardrail_hub.config import DEFAULT_PORT, find_config_path, load_config
from guardrail_hub.errors import ConfigError

MINIMAL = """
[[repos]]
name = "alpha"
path = "/tmp/alpha"
"""


def _write(path: Path, content: str = MINIMAL) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_load_minimal_config(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path / "config.toml"))

    assert [r.name for r in config.repos] == ["alpha"]
    assert config.repos[0].family == "default"
    assert config.repos[0].default_branch == "main"
    assert config.server.port == DEFAULT_PORT


def test_repo_path_expands_user(tmp_path: Path) -> None:
    config = load_config(
        _write(tmp_path / "config.toml", '[[repos]]\nname = "a"\npath = "~/somewhere"\n')
    )

    assert config.repos[0].path == Path.home() / "somewhere"


def test_full_config_round_trip(tmp_path: Path) -> None:
    content = """
log_level = "debug"

[server]
host = "0.0.0.0"
port = 9000

[[repos]]
name = "alpha"
path = "/tmp/alpha"
family = "lithos"
default_branch = "trunk"
"""
    config = load_config(_write(tmp_path / "config.toml", content))

    assert config.log_level == "debug"
    assert config.server.host == "0.0.0.0"
    assert config.server.port == 9000
    entry = config.repos[0]
    assert (entry.family, entry.default_branch) == ("lithos", "trunk")
    assert config.repo("alpha") == entry
    assert config.repo("nope") is None


@pytest.mark.parametrize(
    "content, fragment",
    [
        ("", r"no \[\[repos\]\]"),
        ('[[repos]]\nname = "a"\n', "'path'"),
        ('[[repos]]\nname = ""\npath = "/x"\n', "'name'"),
        ('[[repos]]\nname = "a"\npath = "/x"\n[[repos]]\nname = "a"\npath = "/y"\n', "duplicate"),
        ('[[repos]]\nname = "a"\npath = "/x"\n[server]\nport = "eight"\n', "integer"),
        ("not toml [", "Invalid TOML"),
    ],
)
def test_invalid_configs_raise(tmp_path: Path, content: str, fragment: str) -> None:
    with pytest.raises(ConfigError, match=fragment):
        load_config(_write(tmp_path / "config.toml", content))


def test_env_overrides_beat_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUARDRAIL_HUB_HOST", "10.0.0.1")
    monkeypatch.setenv("GUARDRAIL_HUB_PORT", "1234")
    monkeypatch.setenv("GUARDRAIL_HUB_LOG_LEVEL", "warning")

    config = load_config(_write(tmp_path / "config.toml"))

    assert config.server.host == "10.0.0.1"
    assert config.server.port == 1234
    assert config.log_level == "warning"


def test_bad_port_env_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUARDRAIL_HUB_PORT", "eight")

    with pytest.raises(ConfigError, match="GUARDRAIL_HUB_PORT"):
        load_config(_write(tmp_path / "config.toml"))


def test_find_config_env_var_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit = _write(tmp_path / "explicit.toml")
    monkeypatch.setenv("GUARDRAIL_HUB_CONFIG", str(explicit))

    assert find_config_path() == explicit


def test_find_config_env_var_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GUARDRAIL_HUB_CONFIG", str(tmp_path / "nope.toml"))

    with pytest.raises(ConfigError, match="does not exist"):
        find_config_path()


def test_find_config_cwd_beats_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GUARDRAIL_HUB_CONFIG", raising=False)
    xdg = tmp_path / "xdg"
    (xdg / "guardrail-hub").mkdir(parents=True)
    xdg_config = _write(xdg / "guardrail-hub" / "config.toml")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    assert find_config_path() == xdg_config  # no CWD file -> XDG

    cwd_config = _write(cwd / "config.toml")
    assert find_config_path() == cwd_config  # CWD file wins


def test_find_config_nothing_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GUARDRAIL_HUB_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty-xdg"))
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigError, match="No config found"):
        find_config_path()
