"""Python helpers for the codex CLI bindings."""

from __future__ import annotations

from ._cleon import auth, run  # Re-export PyO3 bindings
from .magic import (
    load_ipython_extension,
    register_codex_magic,
    register_magic,
    use,
    history_magic,
)
from . import autoroute

__all__ = [
    "auth",
    "run",
    "register_magic",
    "register_codex_magic",
    "use",
    "autoroute",
    "load_ipython_extension",
]
