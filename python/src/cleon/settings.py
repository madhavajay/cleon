"""Persistent settings management for cleon."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

DEFAULT_SETTINGS: dict[str, Any] = {
    "default_agent": "codex",
    "default_mode": "learn",
    "plain_text_output": False,
    "agents": {
        "codex": {
            "prefix": "@",
            "default_mode": "learn",
            "binary": None,
            "theme": {
                "light_bg": "#1F1F1F",
                "light_border": "#3A3A3A",
                "light_color": "#F5F5F5",
                "dark_bg": "#1F1F1F",
                "dark_border": "#3A3A3A",
                "dark_color": "#F5F5F5",
            },
        },
        "claude": {
            "prefix": "~",
            "default_mode": "learn",
            "binary": None,
            "connection_url": None,
            "server_dir": None,
            "anthropic_api_key": None,
            "server_command": None,
            "query_config": {},
            "system_prompt": (
                "You are Claude, Anthropic's coding-focused assistant, answering from inside a Jupyter notebook via Cleon. "
                "Never refer to yourself as Pi or pi-coding-agent. Keep replies concise, focus on the requested code/task, "
                "and mention when you ran shell commands or edited files."
            ),
            "allowed_tools": None,
            "provider": "anthropic",
            "model": "claude-sonnet-4-5",
            "args": [],
            "no_session": True,
            "response_timeout": 240,
            "theme": {
                "light_bg": "#262624",
                "light_border": "#4A4A45",
                "light_color": "#F7F5F2",
                "dark_bg": "#262624",
                "dark_border": "#4A4A45",
                "dark_color": "#F7F5F2",
            },
        },
        "gemini": {
            "prefix": ">",
            "default_mode": "learn",
            "binary": None,
            "command": None,
            "model": "gemini-2.5-flash",
            "approval_mode": "auto_edit",  # or "yolo"; kept mild by default
            "allowed_tools": [
                "run_shell_command",
                "read_file",
                "write_file",
            ],
            "args": [],
            "env": {},
            "response_timeout": 240,
            "theme": {
                "light_bg": "#1F2233",
                "light_border": "#2E3A4A",
                "light_color": "#F3F6FF",
                "dark_bg": "#0F1624",
                "dark_border": "#223047",
                "dark_color": "#E4ECFF",
            },
        },
    },
    "modes": {
        "learn": {
            "template": None,
            "agent": None,
        },
        "do": {
            "template": None,
            "agent": None,
        },
    },
}


def get_cleon_home() -> Path:
    path = Path.home() / ".cleon"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_session_store_path() -> Path:
    return get_cleon_home() / ".cleon_session.json"


def _deep_update(target: dict[str, Any], updates: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in updates.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            target[key] = _deep_update(target[key], value)
        else:
            target[key] = value
    return target


def _set_path(target: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cursor = target
    for part in parts[:-1]:
        if part not in cursor or not isinstance(cursor.get(part), dict):
            cursor[part] = {}
        cursor = cursor[part]  # type: ignore[assignment]
    cursor[parts[-1]] = value


def _get_path(source: Mapping[str, Any], path: str) -> Any:
    parts = path.split(".")
    cursor: Any = source
    for part in parts:
        if not isinstance(cursor, Mapping) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


class SettingsManager:
    """Thin helper to persist and mutate cleon settings."""

    def __init__(self) -> None:
        self._path = get_cleon_home() / "settings.json"
        self._cache: dict[str, Any] | None = None

    def load(self) -> dict[str, Any]:
        if self._cache is not None:
            return copy.deepcopy(self._cache)
        data = copy.deepcopy(DEFAULT_SETTINGS)
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    _deep_update(data, raw)
            except Exception:
                pass
        self._cache = data
        return copy.deepcopy(data)

    def save(self, data: dict[str, Any]) -> None:
        serialized = json.dumps(data, ensure_ascii=False, indent=2)
        self._path.write_text(serialized, encoding="utf-8")
        self._cache = copy.deepcopy(data)

    def update(self, updates: Mapping[str, Any]) -> dict[str, Any]:
        data = self.load()
        _deep_update(data, updates)
        self.save(data)
        return data

    def reset(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                self._path.unlink()
            except Exception:
                pass
        self._cache = None
        return self.load()


_SETTINGS_MANAGER = SettingsManager()


def get_settings_manager() -> SettingsManager:
    return _SETTINGS_MANAGER


def load_settings() -> dict[str, Any]:
    return _SETTINGS_MANAGER.load()


def update_settings(updates: Mapping[str, Any]) -> dict[str, Any]:
    return _SETTINGS_MANAGER.update(updates)


def reset_settings() -> dict[str, Any]:
    data = _SETTINGS_MANAGER.reset()
    # Refresh auto-route prefixes in notebooks if available.
    try:
        from . import magic  # type: ignore

        magic.refresh_auto_route()
    except Exception:
        pass
    return data


_UNSET: Any = object()


def settings(key: Any = _UNSET, value: Any = _UNSET, **updates: Any) -> dict[str, Any]:
    """Read or update cleon settings.

    Usage:
    - settings() -> returns all settings
    - settings(foo=True, bar=2) -> updates multiple keys (keys can use dot or __ syntax)
    - settings("foo", True) -> updates a single key (dot paths allowed)
    - settings("foo.bar") -> reads a nested key
    """

    # Read a single path
    if key is not _UNSET and value is _UNSET and not updates:
        path = str(key).replace("__", ".")
        return _get_path(load_settings(), path)  # type: ignore[return-value]

    if key is not _UNSET:
        updates[str(key)] = value

    if not updates:
        return load_settings()

    flattened: dict[str, Any] = {}
    for k, v in updates.items():
        path = str(k).replace("__", ".")
        if "." in path:
            _set_path(flattened, path, v)
        else:
            flattened[path] = v
    return update_settings(flattened)


def get_agent_settings(agent: str) -> dict[str, Any]:
    data = load_settings()
    agents = data.get("agents", {})
    cfg = agents.get(agent, {})
    return copy.deepcopy(cfg)


def get_agent_prefix(agent: str) -> str:
    cfg = get_agent_settings(agent)
    return cfg.get("prefix") or ">"


def get_agent_binary(agent: str) -> str | None:
    cfg = get_agent_settings(agent)
    return cfg.get("binary")


def get_default_mode(agent: str | None = None) -> str:
    data = load_settings()
    if agent:
        agent_cfg = get_agent_settings(agent)
        if agent_cfg.get("default_mode"):
            return agent_cfg["default_mode"]
    return data.get("default_mode", "learn")


def settings_table() -> str:
    """Render a Markdown table summarizing key per-agent settings."""

    data = load_settings()
    agents = data.get("agents", {})
    rows = ["| Agent | Prefix | Model | Command |", "|---|---|---|---|"]
    for name, cfg in agents.items():
        prefix = cfg.get("prefix", "")
        model = cfg.get("model", "")
        command = cfg.get("command") or cfg.get("binary") or "(auto)"
        if isinstance(command, list):
            command = " ".join(str(c) for c in command)
        elif command is None:
            command = "(auto)"
        rows.append(f"| {name} | {prefix} | {model} | {command} |")
    return "\n".join(rows)


def add_mode(
    name: str, template: str | None, *, agent: str | None = None
) -> dict[str, Any]:
    normalized = name.strip().lower()
    return update_settings(
        {
            "modes": {
                normalized: {
                    "template": template,
                    "agent": agent,
                }
            }
        }
    )


def default_mode(name: str, *, agent: str | None = None) -> dict[str, Any]:
    settings_data = load_settings()
    modes = settings_data.get("modes", {})
    normalized = name.strip().lower()
    if normalized not in modes:
        raise ValueError(
            f"Unknown mode '{name}'. Add it with cleon.add_mode(...) first."
        )
    if agent:
        return update_settings({"agents": {agent: {"default_mode": normalized}}})
    return update_settings({"default_mode": normalized})


def plain_text_output() -> bool:
    """Return whether results should render as simple plain text instead of styled HTML."""
    data = load_settings()
    return bool(data.get("plain_text_output", False))


def get_mode_template(mode: str) -> str | None:
    settings_data = load_settings()
    modes = settings_data.get("modes", {})
    entry = modes.get(mode)
    if isinstance(entry, dict):
        template = entry.get("template")
        if isinstance(template, str) or template is None:
            return template or _load_mode_file(mode)
    return None


def template_for_agent(agent: str) -> str | None:
    mode_name = get_default_mode(agent)
    template = get_mode_template(mode_name)
    return template


def _load_mode_file(mode: str) -> str | None:
    """Load a mode template from prompts/<mode>.md if present."""
    try:
        path = Path.cwd() / "prompts" / f"{mode}.md"
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8")
    except Exception:
        pass
    return None


def status_summary() -> dict[str, Any]:
    data = load_settings()
    return {
        "default_agent": data.get("default_agent", "codex"),
        "agents": data.get("agents", {}),
        "modes": data.get("modes", {}),
        "default_mode": data.get("default_mode", "learn"),
    }


def get_agent_theme(agent: str) -> dict[str, str]:
    data = load_settings()
    agents = data.get("agents", {})
    entry = agents.get(agent, {})
    theme = entry.get("theme")
    if isinstance(theme, dict):
        return {str(k): str(v) for k, v in theme.items()}
    return {}
