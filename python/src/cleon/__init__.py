"""Python helpers for the codex CLI bindings."""

from __future__ import annotations

from ._cleon import auth, run  # type: ignore[import-not-found]  # Re-export PyO3 bindings
from .magic import (
    load_ipython_extension,
    register_codex_magic,
    register_magic,
    use,
    history_magic,
    help as help_text,
    stop as stop_session,
    resume as resume_session,
    status as status_info,
    mode as mode_control,
    add_mode as add_mode_entry,
    default_mode as default_mode_entry,
    reset as reset_runtime,
    sessions as list_sessions,
)
from . import autoroute
from .settings import settings as settings_store
from .oauth import login_claude

__all__ = [
    "auth",
    "run",
    "register_magic",
    "register_codex_magic",
    "use",
    "stop",
    "resume",
    "status",
    "mode",
    "add_mode",
    "default_mode",
    "sessions",
    "reset",
    "settings",
    "login",
    "autoroute",
    "load_ipython_extension",
    "history_magic",
    "help",
]


# Expose help() at top-level for convenience
def help() -> None:  # type: ignore[override]
    return help_text()


def stop(agent: str | None = None, *, force: bool = False) -> str | None:
    return stop_session(agent=agent, force=force)


def resume(agent: str = "codex", session_id: str | None = None) -> str | None:
    return resume_session(agent=agent, session_id=session_id)


def status() -> dict[str, object]:
    return status_info()


def mode(name: str | None = None, *, agent: str | None = None) -> str:
    return mode_control(name=name, agent=agent)


def add_mode(name: str, template: str | None = None, *, agent: str | None = None):
    return add_mode_entry(name=name, template=template, agent=agent)


def default_mode(name: str, *, agent: str | None = None):
    return default_mode_entry(name=name, agent=agent)


def settings(**updates):
    return settings_store(**updates)


def reset():
    return reset_runtime()


def sessions():
    return list_sessions()


def login(agent: str = "claude"):
    if agent.lower() in {"claude", "anthropic", "pi"}:
        return login_claude()
    raise ValueError(f"Unknown agent '{agent}'.")


_AUTO_INITIALIZED = False


def _auto_register_magic() -> None:
    global _AUTO_INITIALIZED
    if _AUTO_INITIALIZED:
        return
    try:
        from IPython import get_ipython  # type: ignore

        ip = get_ipython()
        if ip is not None:
            try:
                use(ipython=ip)
            except Exception as exc:
                print(f"Failed to initialize Codex magic: {exc}")
            try:
                register_magic(name="claude", agent="claude", ipython=ip)
            except Exception as exc:
                print(f"Skipping Claude auto-setup: {exc}")
            _AUTO_INITIALIZED = True
    except Exception:
        pass


_auto_register_magic()
