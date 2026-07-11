"""Configuration and registry loading.

The hub is configured by a TOML file that lives OUTSIDE this repo (the registry
of monitored checkouts is machine-local). Discovery follows the lithos-loom
XDG idiom, and parsing/env-overrides follow the lithos-lens idiom: env beats
file beats built-in default.

Discovery order:

1. ``GUARDRAIL_HUB_CONFIG`` env var (explicit path)
2. ``./config.toml`` in CWD (project-local dev override, gitignored)
3. ``$XDG_CONFIG_HOME/guardrail-hub/config.toml`` (fallback ``~/.config/guardrail-hub/``)

The repo ships ``config.example.toml`` as the template.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from guardrail_hub.errors import ConfigError
from guardrail_hub.models import RepoEntry

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_PORT",
    "HubConfig",
    "ServerConfig",
    "find_config_path",
    "load_config",
]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8600
DEFAULT_LOG_LEVEL = "info"

_CONFIG_FILENAME = "config.toml"


# ── Dataclasses ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ServerConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT


@dataclass(frozen=True)
class HubConfig:
    repos: tuple[RepoEntry, ...]
    server: ServerConfig
    log_level: str = DEFAULT_LOG_LEVEL

    def repo(self, name: str) -> RepoEntry | None:
        for entry in self.repos:
            if entry.name == name:
                return entry
        return None


# ── Discovery ──────────────────────────────────────────────────────────


def _config_dir() -> Path:
    """Return the user's guardrail-hub config directory.

    Honours ``XDG_CONFIG_HOME`` per the XDG base-directory spec; falls back
    to ``~/.config/guardrail-hub``.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "guardrail-hub"


def _default_config_candidates() -> list[Path]:
    """Filesystem candidates checked when ``GUARDRAIL_HUB_CONFIG`` is unset."""
    return [
        Path.cwd() / _CONFIG_FILENAME,
        _config_dir() / _CONFIG_FILENAME,
    ]


def find_config_path() -> Path:
    """Locate the active config file via env var or the default candidates."""
    explicit = os.environ.get("GUARDRAIL_HUB_CONFIG")
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"GUARDRAIL_HUB_CONFIG points at {path}, which does not exist")
        return path
    for candidate in _default_config_candidates():
        if candidate.is_file():
            return candidate
    raise ConfigError(
        "No config found. Create "
        f"{_config_dir() / _CONFIG_FILENAME} (see config.example.toml in the "
        "guardrail-hub repo) or set GUARDRAIL_HUB_CONFIG."
    )


# ── Parsing ────────────────────────────────────────────────────────────


def _require_str(table: dict[str, Any], key: str, where: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: {key!r} must be a non-empty string")
    return value.strip()


def _optional_str(table: dict[str, Any], key: str, default: str, where: str) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: {key!r} must be a non-empty string")
    return value.strip()


def _optional_int(table: dict[str, Any], key: str, default: int, where: str) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where}: {key!r} must be an integer")
    return value


def _optional_subdir(table: dict[str, Any], where: str) -> str:
    """A relative path inside the checkout (monorepos); empty = the checkout root."""
    value = table.get("subdir", "")
    if not isinstance(value, str):
        raise ConfigError(f"{where}: 'subdir' must be a string")
    subdir = value.strip().strip("/")
    if subdir and (subdir.startswith("..") or Path(subdir).is_absolute()):
        raise ConfigError(f"{where}: 'subdir' must be a relative path inside the checkout")
    return subdir


def _parse_repos(raw: Any) -> tuple[RepoEntry, ...]:
    if raw is None:
        raise ConfigError("config has no [[repos]] entries — nothing to monitor")
    if not isinstance(raw, list) or not all(isinstance(r, dict) for r in raw):
        raise ConfigError("[[repos]] must be an array of tables")
    entries: list[RepoEntry] = []
    seen: set[str] = set()
    for i, table in enumerate(raw):
        where = f"[[repos]] entry {i + 1}"
        name = _require_str(table, "name", where)
        if name in seen:
            raise ConfigError(f"{where}: duplicate repo name {name!r}")
        seen.add(name)
        path = Path(_require_str(table, "path", where)).expanduser()
        entries.append(
            RepoEntry(
                name=name,
                path=path,
                family=_optional_str(table, "family", "default", where),
                default_branch=_optional_str(table, "default_branch", "main", where),
                subdir=_optional_subdir(table, where),
            )
        )
    return tuple(entries)


def _parse_server(raw: Any) -> ServerConfig:
    if raw is None:
        return ServerConfig()
    if not isinstance(raw, dict):
        raise ConfigError("[server] must be a table")
    return ServerConfig(
        host=_optional_str(raw, "host", DEFAULT_HOST, "[server]"),
        port=_optional_int(raw, "port", DEFAULT_PORT, "[server]"),
    )


def _apply_env_overrides(config: HubConfig) -> HubConfig:
    host = os.environ.get("GUARDRAIL_HUB_HOST")
    port = os.environ.get("GUARDRAIL_HUB_PORT")
    log_level = os.environ.get("GUARDRAIL_HUB_LOG_LEVEL")
    server = config.server
    if host:
        server = replace(server, host=host)
    if port:
        try:
            server = replace(server, port=int(port))
        except ValueError as exc:
            raise ConfigError(f"GUARDRAIL_HUB_PORT must be an integer, got {port!r}") from exc
    result = replace(config, server=server)
    if log_level:
        result = replace(result, log_level=log_level)
    return result


def load_config(path: Path | None = None) -> HubConfig:
    """Load, validate, and env-override the hub config."""
    config_path = path if path is not None else find_config_path()
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Cannot read config {config_path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {config_path}: {exc}") from exc
    config = HubConfig(
        repos=_parse_repos(raw.get("repos")),
        server=_parse_server(raw.get("server")),
        log_level=_optional_str(raw, "log_level", DEFAULT_LOG_LEVEL, "config"),
    )
    return _apply_env_overrides(config)
