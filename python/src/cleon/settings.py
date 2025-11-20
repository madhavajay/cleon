"""Persistent settings management for cleon."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

DEFAULT_SETTINGS: dict[str, Any] = {
    "default_agent": "codex",
    "default_mode": "learn",
    "agents": {
        "codex": {"prefix": ">", "default_mode": "learn", "binary": None},
        "claude": {"prefix": "~", "default_mode": "learn", "binary": None},
    },
    "modes": {
        "learn": {
            "template": None,
            "agent": None,
        },
        "oracle": {
            "template": "Answer succinctly with direct solutions. Avoid speculative language unless clarification is required.",
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
    return _SETTINGS_MANAGER.reset()


def settings(**updates: Any) -> dict[str, Any]:
    """Read or update cleon settings."""

    if not updates:
        return load_settings()
    flattened: dict[str, Any] = {}
    for key, value in updates.items():
        flattened[key] = value
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


def add_mode(name: str, template: str | None, *, agent: str | None = None) -> dict[str, Any]:
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
        raise ValueError(f"Unknown mode '{name}'. Add it with cleon.add_mode(...) first.")
    if agent:
        return update_settings({"agents": {agent: {"default_mode": normalized}}})
    return update_settings({"default_mode": normalized})


def get_mode_template(mode: str) -> str | None:
    settings_data = load_settings()
    modes = settings_data.get("modes", {})
    entry = modes.get(mode)
    if isinstance(entry, dict):
        template = entry.get("template")
        if isinstance(template, str) or template is None:
            return template
    return None


def template_for_agent(agent: str) -> str | None:
    mode_name = get_default_mode(agent)
    template = get_mode_template(mode_name)
    return template


def status_summary() -> dict[str, Any]:
    data = load_settings()
    return {
        "default_agent": data.get("default_agent", "codex"),
        "agents": data.get("agents", {}),
        "modes": data.get("modes", {}),
        "default_mode": data.get("default_mode", "learn"),
    }
