"""Python helpers for the codex CLI bindings."""

from __future__ import annotations

import importlib.util
import os

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
    "check_extension",
]


def has_extension(*, verbose: bool = True) -> bool:
    """Check if cleon-jupyter-extension is installed and available.

    Returns True if the extension package is installed.
    If verbose=True (default), also displays status about whether the
    extension is loaded and reachable in the current notebook.
    """
    installed = importlib.util.find_spec("cleon_cell_control") is not None

    if verbose:
        _display_extension_status(installed)

    return installed


def _is_vscode_notebook() -> bool:
    env = os.environ
    return bool(
        env.get("VSCODE_PID")
        or env.get("VSCODE_CWD")
        or env.get("TERM_PROGRAM") == "vscode"
    )


def _display_extension_status(installed: bool) -> None:
    """Display styled extension status information."""
    try:
        from IPython.display import display, HTML
    except ImportError:
        # Plain text fallback
        if installed:
            print("✅ Extension installed")
            print("   Run: cleon.check_extension() to test if it's loaded")
        else:
            print("❌ Extension not installed")
            print("   Run: cleon.install_extension()")
        return

    if _is_vscode_notebook():
        html = """
<div style="background:#2a2a1a; border:1px solid #5a2d2d; border-radius:8px; padding:12px 16px; margin:8px 0; font-family:system-ui,-apple-system,sans-serif;">
<div style="color:#FFD700; font-weight:600; margin-bottom:8px;">⚠️ VS Code notebooks detected</div>
<div style="color:#f5d4d4; font-size:0.9em; line-height:1.5;">
<p style="margin:0 0 8px 0;">VS Code’s notebook UI does not load JupyterLab extensions, so Cleon’s interactive buttons are unavailable here.</p>
<p style="margin:0 0 4px 0;">Use <code>cleon jupyter lab</code> in a browser for the full UI, or keep using the Python magics in VS Code.</p>
</div>
</div>"""
    elif installed:
        html = """
<div style="background:#1a2e1a; border:1px solid #2d5a2d; border-radius:8px; padding:12px 16px; margin:8px 0; font-family:system-ui,-apple-system,sans-serif;">
<div style="color:#90EE90; font-weight:600; margin-bottom:8px;">✅ Extension Installed</div>
<div style="color:#d4edda; font-size:0.9em; line-height:1.5;">
<p style="margin:0 0 8px 0;">The extension package is installed. To verify it's loaded in JupyterLab:</p>
<div style="position:relative; background:#272822; border-radius:6px; padding:8px 50px 8px 12px; margin:8px 0;">
<code style="color:#f8f8f2; font-family:'Fira Code',monospace; font-size:0.85em;">cleon.check_extension()</code>
<button onclick="navigator.clipboard.writeText('cleon.check_extension()').then(() => { this.textContent='✓'; setTimeout(() => this.textContent='📋', 1500); })" style="position:absolute; right:6px; top:50%; transform:translateY(-50%); background:rgba(255,255,255,0.1); border:none; border-radius:4px; padding:4px 8px; cursor:pointer; color:#f8f8f2; font-size:12px; opacity:0.7;" onmouseover="this.style.opacity='1'" onmouseout="this.style.opacity='0.7'" title="Copy">📋</button>
</div>
<p style="margin:8px 0 0 0; color:#a8d8a8; font-size:0.85em;">If not loaded, restart JupyterLab and refresh the page.</p>
</div>
</div>"""
    else:
        html = """
<div style="background:#2a1a1a; border:1px solid #5a2d2d; border-radius:8px; padding:12px 16px; margin:8px 0; font-family:system-ui,-apple-system,sans-serif;">
<div style="color:#ff6b6b; font-weight:600; margin-bottom:8px;">❌ Extension Not Installed</div>
<div style="color:#f5d4d4; font-size:0.9em; line-height:1.5;">
<p style="margin:0 0 8px 0;">Install the extension to enable ▶ play buttons on code snippets:</p>
<div style="position:relative; background:#272822; border-radius:6px; padding:8px 50px 8px 12px; margin:8px 0;">
<code style="color:#f8f8f2; font-family:'Fira Code',monospace; font-size:0.85em;">cleon.install_extension()</code>
<button onclick="navigator.clipboard.writeText('cleon.install_extension()').then(() => { this.textContent='✓'; setTimeout(() => this.textContent='📋', 1500); })" style="position:absolute; right:6px; top:50%; transform:translateY(-50%); background:rgba(255,255,255,0.1); border:none; border-radius:4px; padding:4px 8px; cursor:pointer; color:#f8f8f2; font-size:12px; opacity:0.7;" onmouseover="this.style.opacity='1'" onmouseout="this.style.opacity='0.7'" title="Copy">📋</button>
</div>
</div>
</div>"""

    display(HTML(html))


def check_extension() -> bool:
    """Check if the extension is loaded and reachable in the current notebook.

    This tests whether window.cleonInsertAndRun is available in the browser.
    Returns True if the extension is working, False otherwise.
    """
    try:
        from IPython.display import display, HTML
    except ImportError:
        print("Not running in IPython/Jupyter")
        return False

    if _is_vscode_notebook():
        display(
            HTML(
                """
<div style=\"background:#2a2a1a; border:1px solid #5a5a2d; border-radius:8px; padding:12px 16px; margin:8px 0; font-family:system-ui,-apple-system,sans-serif;\">
<div style=\"color:#FFD700; font-weight:600; margin-bottom:8px;\">⚠️ VS Code notebooks do not load JupyterLab extensions</div>
<div style=\"color:#f5d4d4; font-size:0.9em; line-height:1.5;\">
<p style=\"margin:0 0 8px 0;\">The Cleon extension UI is unavailable in VS Code. Use <code>cleon jupyter lab</code> in a browser for buttons/controls, or continue using the Python magics here.</p>
</div>
</div>
"""
            )
        )
        return False

    # Inject JavaScript to check and report
    js_check = """
<div id="cleon-ext-check" style="background:#1F1F1F; border:1px solid #3A3A3A; border-radius:8px; padding:12px 16px; margin:8px 0; font-family:system-ui,-apple-system,sans-serif;">
<div style="color:#F5F5F5; font-size:0.9em;">Checking extension status...</div>
</div>
<script>
(function() {
    var container = document.getElementById('cleon-ext-check');
    if (window.cleonInsertAndRun) {
        container.style.background = '#1a2e1a';
        container.style.borderColor = '#2d5a2d';
        container.innerHTML = '<div style="color:#90EE90; font-weight:600;">✅ Extension Loaded & Working</div><div style="color:#d4edda; font-size:0.9em; margin-top:6px;">Code snippets will have ▶ play buttons to insert & run.</div>';
    } else {
        container.style.background = '#2a2a1a';
        container.style.borderColor = '#5a5a2d';
        container.innerHTML = '<div style="color:#FFD700; font-weight:600;">⚠️ Extension Not Loaded</div><div style="color:#f5f5d4; font-size:0.9em; margin-top:6px; line-height:1.5;"><p style="margin:0 0 6px 0;">The extension is installed but not loaded in this session.</p><p style="margin:0;"><strong>Fix:</strong> Restart JupyterLab, then refresh this page.</p></div>';
    }
})();
</script>
"""
    display(HTML(js_check))
    return has_extension(verbose=False)


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

    try:
        from IPython.display import display, HTML

        use_html = True
    except ImportError:
        use_html = False

    # Check if we're in dev mode
    if os.environ.get("CLEON_DEV_MODE"):
        msg = "⚠️  Dev mode detected. Extension should be installed via: ./jupyter.sh"
        if use_html:
            display(
                HTML(f"""
<div style="background:#2a2a1a; border:1px solid #5a5a2d; border-radius:8px; padding:12px 16px; margin:8px 0;">
<div style="color:#FFD700;">{msg}</div>
</div>""")
            )
        else:
            print(msg)
        return

    # Check if already installed
    if has_extension(verbose=False):
        if use_html:
            display(
                HTML("""
<div style="background:#1a2e1a; border:1px solid #2d5a2d; border-radius:8px; padding:12px 16px; margin:8px 0; font-family:system-ui,-apple-system,sans-serif;">
<div style="color:#90EE90; font-weight:600; margin-bottom:8px;">✅ Already Installed</div>
<div style="color:#d4edda; font-size:0.9em; line-height:1.5;">
<p style="margin:0 0 8px 0;">The extension is already installed. Check if it's loaded:</p>
<div style="position:relative; background:#272822; border-radius:6px; padding:8px 50px 8px 12px; margin:8px 0;">
<code style="color:#f8f8f2; font-family:'Fira Code',monospace; font-size:0.85em;">cleon.check_extension()</code>
<button onclick="navigator.clipboard.writeText('cleon.check_extension()').then(() => { this.textContent='✓'; setTimeout(() => this.textContent='📋', 1500); })" style="position:absolute; right:6px; top:50%; transform:translateY(-50%); background:rgba(255,255,255,0.1); border:none; border-radius:4px; padding:4px 8px; cursor:pointer; color:#f8f8f2; font-size:12px; opacity:0.7;" onmouseover="this.style.opacity='1'" onmouseout="this.style.opacity='0.7'" title="Copy">📋</button>
</div>
<p style="margin:8px 0 0 0; color:#a8d8a8; font-size:0.85em;">If not working, restart JupyterLab and refresh the page.</p>
</div>
</div>""")
            )
        else:
            print("✅ cleon-jupyter-extension is already installed!")
            print("   Run cleon.check_extension() to verify it's loaded.")
        return

    # Determine install command based on environment
    use_uv = _is_uv_environment()

    if use_uv:
        cmd = ["uv", "pip", "install", "-U", "cleon-jupyter-extension"]
        cmd_str = "uv pip install -U cleon-jupyter-extension"
    else:
        cmd = [sys.executable, "-m", "pip", "install", "-U", "cleon-jupyter-extension"]
        cmd_str = "pip install -U cleon-jupyter-extension"

    if use_html:
        display(
            HTML(f"""
<div style="background:#1F1F1F; border:1px solid #3A3A3A; border-radius:8px; padding:12px 16px; margin:8px 0;">
<div style="color:#F5F5F5;">📦 Installing cleon-jupyter-extension...</div>
<div style="color:#888; font-size:0.85em; margin-top:4px;">Command: {cmd_str}</div>
</div>""")
        )
    else:
        print("📦 Installing cleon-jupyter-extension...")
        print(f"   Command: {cmd_str}")

    try:
        subprocess.check_call(cmd)

        if use_html:
            display(
                HTML("""
<div style="background:#1a2e1a; border:1px solid #2d5a2d; border-radius:8px; padding:12px 16px; margin:8px 0; font-family:system-ui,-apple-system,sans-serif;">
<div style="color:#90EE90; font-weight:600; margin-bottom:10px;">✅ Installation Complete!</div>
<div style="color:#d4edda; font-size:0.9em; line-height:1.6;">
<div style="background:#2d5a2d; border-radius:6px; padding:10px 12px; margin-bottom:10px;">
<div style="color:#FFD700; font-weight:600; margin-bottom:6px;">⚠️ RESTART REQUIRED</div>
<div style="color:#d4edda;">
<strong>1.</strong> Save your work<br/>
<strong>2.</strong> Stop JupyterLab (Ctrl+C in terminal)<br/>
<strong>3.</strong> Start JupyterLab again
</div>
</div>
<p style="margin:8px 0;">After restart, verify it's working:</p>
<div style="position:relative; background:#272822; border-radius:6px; padding:8px 50px 8px 12px; margin:8px 0;">
<code style="color:#f8f8f2; font-family:'Fira Code',monospace; font-size:0.85em;">cleon.check_extension()</code>
<button onclick="navigator.clipboard.writeText('cleon.check_extension()').then(() => { this.textContent='✓'; setTimeout(() => this.textContent='📋', 1500); })" style="position:absolute; right:6px; top:50%; transform:translateY(-50%); background:rgba(255,255,255,0.1); border:none; border-radius:4px; padding:4px 8px; cursor:pointer; color:#f8f8f2; font-size:12px; opacity:0.7;" onmouseover="this.style.opacity='1'" onmouseout="this.style.opacity='0.7'" title="Copy">📋</button>
</div>
</div>
</div>""")
            )
        else:
            print("\n✅ Installation complete!")
            print("\n⚠️  RESTART REQUIRED:")
            print("   1. Save your work")
            print("   2. Stop JupyterLab (Ctrl+C in terminal)")
            print("   3. Start JupyterLab again")
            print("\nAfter restart, run: cleon.check_extension()")

    except subprocess.CalledProcessError as e:
        fallback_cmd = (
            "uv pip install -U cleon-jupyter-extension"
            if use_uv
            else "pip install -U cleon-jupyter-extension"
        )
        if use_html:
            display(
                HTML(f"""
<div style="background:#2a1a1a; border:1px solid #5a2d2d; border-radius:8px; padding:12px 16px; margin:8px 0; font-family:system-ui,-apple-system,sans-serif;">
<div style="color:#ff6b6b; font-weight:600; margin-bottom:8px;">❌ Installation Failed</div>
<div style="color:#f5d4d4; font-size:0.9em; line-height:1.5;">
<p style="margin:0 0 8px 0;">Error: {e}</p>
<p style="margin:0 0 8px 0;">Try manually in your terminal:</p>
<div style="position:relative; background:#272822; border-radius:6px; padding:8px 50px 8px 12px; margin:8px 0;">
<code style="color:#f8f8f2; font-family:'Fira Code',monospace; font-size:0.85em;">{fallback_cmd}</code>
<button onclick="navigator.clipboard.writeText('{fallback_cmd}').then(() => {{ this.textContent='✓'; setTimeout(() => this.textContent='📋', 1500); }})" style="position:absolute; right:6px; top:50%; transform:translateY(-50%); background:rgba(255,255,255,0.1); border:none; border-radius:4px; padding:4px 8px; cursor:pointer; color:#f8f8f2; font-size:12px; opacity:0.7;" onmouseover="this.style.opacity='1'" onmouseout="this.style.opacity='0.7'" title="Copy">📋</button>
</div>
</div>
</div>""")
            )
        else:
            print(f"❌ Installation failed: {e}")
            print(f"   Try manually: {fallback_cmd}")


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
                _render_upgrade_notice(current, latest, cmd, use_uv)

        except Exception:
            # Silently fail - version check is non-critical
            pass

    # Run in background thread to not block import
    thread = threading.Thread(target=_do_check, daemon=True)
    thread.start()


def _render_upgrade_notice(current: str, latest: str, cmd: str, use_uv: bool) -> None:
    """Display upgrade message with copy/run helpers (best-effort)."""

    # Try rich HTML first
    try:
        from IPython.display import display, HTML

        ext_installed = has_extension(verbose=False)
        in_vscode = _is_vscode_notebook()
        cmd_html = cmd.replace('"', '\\"')
        run_btn = ""
        if ext_installed and not in_vscode:
            run_btn = f"""
<button onclick=\"(function(btn) {{
  const cmd = '{cmd_html}';
  if (window.cleonInsertAndRun) {{
    window.cleonInsertAndRun('!'+cmd);
    btn.textContent = '✓ running';
    setTimeout(() => btn.textContent = '▶ Run upgrade', 2000);
  }} else {{
    navigator.clipboard.writeText(cmd).then(() => {{
      btn.textContent = '✓ copied';
      setTimeout(() => btn.textContent = '▶ Run upgrade', 1500);
    }});
  }}
}})(this)\" style=\"margin-left:8px; background:#2d5a2d; color:#f5f5f5; border:none; border-radius:4px; padding:6px 10px; cursor:pointer;\">▶ Run upgrade</button>
"""
        copy_btn = f"""
<button onclick=\"navigator.clipboard.writeText('{cmd_html}').then(() => {{ this.textContent='✓'; setTimeout(() => this.textContent='📋', 1500); }});\" style=\"position:absolute; right:6px; top:50%; transform:translateY(-50%); background:rgba(255,255,255,0.1); border:none; border-radius:4px; padding:4px 8px; cursor:pointer; color:#f8f8f2; font-size:12px; opacity:0.8;\" onmouseover=\"this.style.opacity='1'\" onmouseout=\"this.style.opacity='0.8'\" title=\"Copy\">📋</button>
"""

        display(
            HTML(
                f"""
<div style="background:#1F1F1F; border:1px solid #3A3A3A; border-radius:10px; padding:12px 14px; margin:10px 0; font-family:system-ui,-apple-system,sans-serif; box-shadow:0 3px 10px rgba(0,0,0,0.25);">
  <div style=\"color:#f5f5f5; font-weight:600; margin-bottom:6px;\">📦 Cleon update available</div>
  <div style=\"color:#dcdcdc; font-size:0.9em; margin-bottom:10px;\">Installed: <code style=\"color:#f8f8f2;\">{current}</code> &nbsp;→&nbsp; Latest: <code style=\"color:#f8f8f2;\">{latest}</code></div>
  <div style="color:#dcdcdc; font-size:0.9em; margin-bottom:10px;">Upgrade with:</div>
  <div style="position:relative; background:#272822; border-radius:6px; padding:8px 12px; color:#f8f8f2; font-family:'Fira Code',monospace; font-size:0.9em; line-height:1.4;">
    {cmd}
    {copy_btn}
  </div>
  <div style="margin-top:10px;">
    <span style="color:#a0a0a0; font-size:0.85em;">Env: {"uv" if use_uv else "pip"}</span>
    {run_btn}
  </div>
</div>
"""
            )
        )
        return
    except Exception:
        # Fallback to plain text
        pass

    print(f"\n📦 New cleon version available: {current} → {latest}")
    print(f"   Upgrade: {cmd}\n")


def _display_welcome_message() -> None:
    """Display styled welcome message with agent prefixes and extension hint."""
    try:
        from IPython.display import display, HTML
    except ImportError:
        # Plain text fallback
        print("Cleon session started.\n")
        print("Pick an agent with a prefix:\n")
        print("@ Whats your name?")
        print("I am Codex!\n")
        print("~ Whats your name?")
        print("I'm Claude, Anthropic's AI assistant!\n")
        print("> Whats your name?")
        print("I'm Gemini, Google's AI assistant!")
        return

    ext_installed = has_extension(verbose=False)
    in_vscode = _is_vscode_notebook()

    # Extension status section
    if in_vscode:
        ext_html = """
<div style="background:#2a1a1a; border:1px solid #5a2d2d; border-radius:6px; padding:10px 12px; margin-top:12px;">
<div style="color:#FFD700; font-size:0.85em; margin-bottom:6px;">⚠️ VS Code notebooks detected</div>
<div style="color:#f5d4d4; font-size:0.85em; line-height:1.5;">VS Code does not load JupyterLab extensions, so the Cleon UI buttons are unavailable here. Use <code>cleon jupyter lab</code> in a browser for the full UI, or keep using the magics in VS Code.</div>
</div>"""
    elif ext_installed:
        ext_html = """
<div style="background:#1a2e1a; border:1px solid #2d5a2d; border-radius:6px; padding:10px 12px; margin-top:12px;">
<div style="color:#90EE90; font-size:0.85em;">✅ Extension installed — code snippets have ▶ play buttons</div>
</div>"""
    else:
        ext_html = """
<div style="background:#262624; border:1px solid #4A4A45; border-radius:6px; padding:10px 12px; margin-top:12px;">
<div style="color:#F7F5F2; font-size:0.85em; margin-bottom:6px;">💡 <strong>Enable ▶ play buttons on code snippets:</strong></div>
<div style="position:relative; background:#1a1a18; border-radius:4px; padding:6px 50px 6px 10px; margin-top:6px;">
<code style="color:#f8f8f2; font-family:'Fira Code',monospace; font-size:0.8em;">cleon.install_extension()</code>
<button onclick="navigator.clipboard.writeText('cleon.install_extension()').then(() => { this.textContent='✓'; setTimeout(() => this.textContent='📋', 1500); })" style="position:absolute; right:4px; top:50%; transform:translateY(-50%); background:rgba(255,255,255,0.1); border:none; border-radius:3px; padding:3px 6px; cursor:pointer; color:#f8f8f2; font-size:11px; opacity:0.7;" onmouseover="this.style.opacity='1'" onmouseout="this.style.opacity='0.7'" title="Copy">📋</button>
</div>
</div>"""

    html = f"""
<div style="background:#1F1F1F; border:1px solid #3A3A3A; border-radius:10px; padding:14px 18px; margin:8px 0; font-family:system-ui,-apple-system,sans-serif; box-shadow:0 4px 12px rgba(0,0,0,0.15);">
<div style="color:#F5F5F5; font-weight:600; font-size:1em; margin-bottom:10px;">Cleon session started.</div>

<div style="color:#aaa; font-size:0.85em; margin-bottom:8px;">Pick an agent with a prefix:</div>

<div style="background:#272822; border-radius:6px; padding:10px 14px; margin-bottom:4px; font-family:'Fira Code',monospace; font-size:0.85em; line-height:1.6;">
<div><span style="color:#66d9ef;">@</span> <span style="color:#f8f8f2;">Whats your name?</span></div>
<div style="color:#a6e22e; margin-left:12px;">I am Codex!</div>
<div style="margin-top:8px;"><span style="color:#f92672;">~</span> <span style="color:#f8f8f2;">Whats your name?</span></div>
<div style="color:#fd971f; margin-left:12px;">I'm Claude, Anthropic's AI assistant!</div>
<div style="margin-top:8px;"><span style="color:#ae81ff;">&gt;</span> <span style="color:#f8f8f2;">Whats your name?</span></div>
<div style="color:#e6db74; margin-left:12px;">I'm Gemini, Google's AI assistant!</div>
</div>

{ext_html}
</div>
"""
    display(HTML(html))


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
                use(ipython=ip, quiet=True)
            except Exception as exc:
                print(f"Failed to initialize Codex magic: {exc}")
            try:
                register_magic(name="claude", agent="claude", ipython=ip, quiet=True)
            except Exception as exc:
                print(f"Skipping Claude auto-setup: {exc}")
            # Register all agents from settings (including gemini)
            try:
                refresh_auto_route(ipython=ip)
            except Exception as exc:
                print(f"Failed to refresh auto-route: {exc}")

            # Show styled welcome message (unless in dev mode)
            if not _EXTENSION_HINT_SHOWN and not os.environ.get("CLEON_DEV_MODE"):
                _display_welcome_message()
                _EXTENSION_HINT_SHOWN = True

            # Check for updates (10% of the time, in background)
            _check_for_updates()

            _AUTO_INITIALIZED = True
    except Exception:
        pass


_auto_register_magic()
