"""Auto-route notebook cells to the %%codex magic without typing it."""

from __future__ import annotations

from typing import Any
from IPython import get_ipython  # type: ignore

_ENABLED = False
_DEFAULT_MODE: str | None = None  # "codex" to auto-route all code cells


def _pre_run_cell(info: Any) -> None:
    """IPython pre-run hook to prepend %%codex when enabled."""
    if not _ENABLED:
        return
    raw = info.raw_cell
    if not isinstance(raw, str):
        return
    if raw.strip().startswith("%%"):
        return
    # Only route code cells; markdown cells are skipped by the kernel
    if _DEFAULT_MODE == "codex":
        info.raw_cell = "%%codex\n" + raw


def cell_default(mode: str | None = None) -> None:
    """Set the default routing mode. Pass \"codex\" to auto-prepend %%codex."""
    global _DEFAULT_MODE
    if mode is None:
        _DEFAULT_MODE = None
    elif mode.lower() == "codex":
        _DEFAULT_MODE = "codex"
    else:
        raise ValueError("Unknown mode. Use None or 'codex'.")


def enable() -> None:
    """Enable auto-routing."""
    global _ENABLED
    if _ENABLED:
        return
    ip = get_ipython()
    if ip is None:
        raise RuntimeError("No active IPython session")
    ip.events.register("pre_run_cell", _pre_run_cell)
    _ENABLED = True


def disable() -> None:
    """Disable auto-routing."""
    global _ENABLED
    if not _ENABLED:
        return
    ip = get_ipython()
    if ip is None:
        return
    ip.events.unregister("pre_run_cell", _pre_run_cell)
    _ENABLED = False


def load_ipython_extension(_ip) -> None:
    enable()


def unload_ipython_extension(_ip) -> None:
    disable()
