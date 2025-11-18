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
)
from . import autoroute

__all__ = [
    "auth",
    "run",
    "register_magic",
    "register_codex_magic",
    "use",
    "stop",
    "autoroute",
    "load_ipython_extension",
    "history_magic",
    "help",
]


# Expose help() at top-level for convenience
def help() -> str:  # type: ignore[override]
    return help_text()


def stop() -> str | None:
    return stop_session()
