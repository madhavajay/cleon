"""Python helpers for the codex CLI bindings."""

from __future__ import annotations

from ._cleon import auth as _codex_auth, run  # type: ignore[import-not-found]  # Re-export PyO3 bindings
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
    refresh_auto_route,
)
from .backend import SharedSession
from . import autoroute
from .settings import settings as settings_store, _UNSET as _SETTINGS_UNSET
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
    "SharedSession",
    "install_extension",
    "has_extension",
]


def has_extension() -> bool:
    """Check if cleon-jupyter-extension is installed and available."""
    import importlib.util

    return importlib.util.find_spec("cleon_cell_control") is not None


def install_extension() -> None:
    """Install the cleon-jupyter-extension for advanced notebook features.

    This extension enables:
    - Insert & run code snippets directly into cells below
    - Programmatic cell manipulation from Python

    After installation, restart JupyterLab to activate the extension.
    """
    import os
    import subprocess
    import sys

    # Check if we're in dev mode
    if os.environ.get("CLEON_DEV_MODE"):
        print("⚠️  Dev mode detected (CLEON_DEV_MODE is set)")
        print("   Extension should be installed via: ./jupyter.sh")
        return

    # Check if already installed
    if has_extension():
        print("✅ cleon-jupyter-extension is already installed!")
        print("   If JupyterLab doesn't show the extension, restart JupyterLab.")
        return

    print("📦 Installing cleon-jupyter-extension...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "cleon-jupyter-extension"]
        )
        print("")
        print("✅ Installation complete!")
        print("")
        print("⚠️  IMPORTANT: You must restart JupyterLab for the extension to load.")
        print("   1. Save your work")
        print("   2. Stop JupyterLab (Ctrl+C in terminal)")
        print("   3. Start JupyterLab again")
        print("")
        print("After restart, code snippets will have a ▶ button to insert & run.")
    except subprocess.CalledProcessError as e:
        print(f"❌ Installation failed: {e}")
        print("   Try manually: pip install cleon-jupyter-extension")


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


def settings(key=_SETTINGS_UNSET, value=_SETTINGS_UNSET, **updates):
    return settings_store(key=key, value=value, **updates)


def reset():
    return reset_runtime()


def sessions():
    return list_sessions()


def login(agent: str = "claude"):
    if agent.lower() in {"claude", "anthropic", "pi"}:
        return login_claude()
    raise ValueError(f"Unknown agent '{agent}'.")


def auth(provider: str | None = None) -> None:
    """Authenticate with the specified provider (defaults to claude/pi)."""
    provider = provider or "claude"
    if provider.lower() in {"claude", "anthropic", "pi"}:
        return login_claude()
    elif provider.lower() == "codex":
        return _codex_auth(provider)
    else:
        raise ValueError(f"Unknown provider '{provider}'. Supported: claude, codex")


_AUTO_INITIALIZED = False


_EXTENSION_HINT_SHOWN = False


_VERSION_CHECK_DONE = False


def _get_current_version() -> str:
    """Get the currently installed version of cleon."""
    try:
        from importlib.metadata import version

        return version("cleon")
    except Exception:
        return "unknown"


def _is_uv_environment() -> bool:
    """Detect if we're running in a uv-managed environment."""
    import os

    # Check for UV_* environment variables
    if any(k.startswith("UV_") for k in os.environ):
        return True

    # Check if uv is in the path and this venv was created by uv
    venv_path = os.environ.get("VIRTUAL_ENV", "")
    if venv_path:
        # uv creates a .uv marker or uses specific structure
        uv_marker = os.path.join(venv_path, ".uv")
        if os.path.exists(uv_marker):
            return True

    # Check if 'uv' command is available and recently used
    try:
        import shutil

        if shutil.which("uv"):
            # Check pyvenv.cfg for uv signature
            if venv_path:
                cfg_path = os.path.join(venv_path, "pyvenv.cfg")
                if os.path.exists(cfg_path):
                    with open(cfg_path) as f:
                        content = f.read()
                        if "uv" in content.lower():
                            return True
    except Exception:
        pass

    return False


def _check_for_updates() -> None:
    """Check PyPI for newer version (runs 10% of the time)."""
    global _VERSION_CHECK_DONE
    if _VERSION_CHECK_DONE:
        return
    _VERSION_CHECK_DONE = True

    import random

    if random.random() > 0.10:  # Only check 10% of the time
        return

    import threading

    def _do_check():
        try:
            import urllib.request
            import json

            current = _get_current_version()
            if current == "unknown":
                return

            # Fetch latest version from PyPI
            url = "https://pypi.org/pypi/cleon/json"
            req = urllib.request.Request(
                url, headers={"User-Agent": "cleon-version-check"}
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode())
                latest = data.get("info", {}).get("version", "")

            if not latest or latest == current:
                return

            # Compare versions
            def _parse_version(v: str) -> tuple:
                """Parse version string into comparable tuple."""
                parts: list[int | str] = []
                for part in v.split("."):
                    try:
                        parts.append(int(part))
                    except ValueError:
                        parts.append(part)
                return tuple(parts)

            if _parse_version(latest) > _parse_version(current):
                use_uv = _is_uv_environment()
                cmd = "uv pip install -U cleon" if use_uv else "pip install -U cleon"

                print(f"\n📦 New cleon version available: {current} → {latest}")
                print(f"   Upgrade: {cmd}\n")

        except Exception:
            # Silently fail - version check is non-critical
            pass

    # Run in background thread to not block import
    thread = threading.Thread(target=_do_check, daemon=True)
    thread.start()


def _auto_register_magic() -> None:
    global _AUTO_INITIALIZED, _EXTENSION_HINT_SHOWN
    if _AUTO_INITIALIZED:
        return
    try:
        from IPython import get_ipython  # type: ignore
        import os

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
            # Register all agents from settings (including gemini)
            try:
                refresh_auto_route(ipython=ip)
            except Exception as exc:
                print(f"Failed to refresh auto-route: {exc}")

            # Show extension hint once (unless in dev mode)
            if not _EXTENSION_HINT_SHOWN and not os.environ.get("CLEON_DEV_MODE"):
                if not has_extension():
                    print("💡 Tip: Run cleon.install_extension() for advanced features")
                    print("   (insert & run code snippets directly into cells)")
                _EXTENSION_HINT_SHOWN = True

            # Check for updates (10% of the time, in background)
            _check_for_updates()

            _AUTO_INITIALIZED = True
    except Exception:
        pass


_auto_register_magic()
