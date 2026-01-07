"""OAuth helpers for Cleon."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from pathlib import Path
from secrets import token_bytes
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlencode

CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
SCOPES = "org:create_api_key user:profile user:inference"
USER_AGENT = "cleon-oauth/0.1 (+https://github.com/cleon-ai/cleon)"


def _oauth_file() -> Path:
    config_dir = Path.home() / ".pi" / "agent"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "oauth.json"


def _generate_pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(token_bytes(32)).decode("utf-8").rstrip("=")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest())
        .decode("utf-8")
        .rstrip("=")
    )
    return verifier, challenge


def _build_auth_url(challenge: str, verifier: str) -> str:
    params = {
        "code": "true",
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": verifier,
    }
    # URL-encode parameters so spaces and punctuation in scope/state do not break the link
    query = urlencode(params, doseq=False)
    return f"{AUTHORIZE_URL}?{query}"


def _save_credentials(provider: str, credentials: dict[str, Any]) -> None:
    path = _oauth_file()
    storage = {}
    if path.exists():
        try:
            storage = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            storage = {}
    storage[provider] = credentials
    path.write_text(json.dumps(storage, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)


def login_pi() -> None:
    """Run pi login to authenticate with Claude via the pi CLI."""
    import subprocess
    import shutil

    pi_cmd = shutil.which("pi")
    if not pi_cmd:
        print(
            "Error: pi CLI not found. Install it via `npm install -g @anthropic-ai/claude-code`"
        )
        return

    print("Running pi login...")
    try:
        result = subprocess.run([pi_cmd, "login"], check=False)
        if result.returncode == 0:
            print("pi login successful.")
            _refresh_active_claude_backend()
        else:
            print(f"pi login exited with code {result.returncode}")
    except Exception as e:
        print(f"Failed to run pi login: {e}")


def login_claude() -> None:
    """Interactive OAuth login for Claude Pro/Max subscriptions."""

    verifier, challenge = _generate_pkce()
    auth_url = _build_auth_url(challenge, verifier)
    print("Open the following URL in your browser and authorize cleon:\n")
    print(auth_url)
    print(
        '\nAfter authorizing, copy the complete "code#state" value and paste it below.'
    )
    code_input = input("Authorization response (code#state): ").strip()
    if not code_input:
        print("Login cancelled.")
        return
    if "#" not in code_input:
        print("Invalid response. Expected format: <code>#<state>")
        return
    code, state = code_input.split("#", 1)
    payload = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "state": state,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    }
    req = Request(
        TOKEN_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        # Surface a concise, user-friendly error instead of a traceback
        detail = ""
        try:
            raw = exc.read().decode("utf-8", errors="ignore")
            if raw:
                # Trim noisy HTML while keeping a useful snippet
                detail = raw.strip()
                max_len = 600
                if len(detail) > max_len:
                    detail = detail[:max_len] + "... (truncated)"
        except Exception:
            pass
        message = f"OAuth token exchange failed ({exc.code} {exc.reason})."
        if exc.code == 403:
            message += " This usually means the code was already used or expired—try restarting login and paste the new code#state."
        if detail:
            message += f" Response: {detail}"
        print(message)
        print("If this keeps failing, set ANTHROPIC_API_KEY directly to skip OAuth.")
        return
    except URLError as exc:
        print(f"OAuth token exchange failed: {exc.reason}")
        return
    expires_ms = int(data.get("expires_in", 0)) * 1000
    now_ms = int(time.time() * 1000)
    expires_at = now_ms + expires_ms - 5 * 60 * 1000
    credentials = {
        "type": "oauth",
        "refresh": data.get("refresh_token"),
        "access": data.get("access_token"),
        "expires": expires_at,
    }
    _save_credentials("anthropic", credentials)
    print("Claude login successful. Tokens stored in ~/.pi/agent/oauth.json.")
    _refresh_active_claude_backend()


def _refresh_active_claude_backend() -> None:
    """If a Claude backend is already loaded, restart it to pick up the new tokens."""
    try:
        from . import magic  # Lazy import to avoid circular imports at module load
    except Exception:
        return
    for key in ("claude", "anthropic"):
        backend = getattr(magic, "_BACKENDS", {}).get(key)  # type: ignore[attr-defined]
        if backend is None:
            continue
        restart = getattr(backend, "restart", None)
        if callable(restart):
            try:
                restart()
                print("Refreshed Claude backend with new credentials.")
            except Exception:
                pass
        break


def _refresh_active_codex_backend() -> None:
    """If a Codex backend is already loaded, restart it to pick up the new tokens."""
    try:
        from . import magic  # Lazy import to avoid circular imports at module load
    except Exception:
        return
    for key in ("codex", "openai-codex"):
        backend = getattr(magic, "_BACKENDS", {}).get(key)  # type: ignore[attr-defined]
        if backend is None:
            continue
        restart = getattr(backend, "restart", None)
        if callable(restart):
            try:
                restart()
                print("Refreshed Codex backend with new credentials.")
            except Exception:
                pass
        break


# ============================================================================
# pi-mono-rust based OAuth functions (recommended)
# ============================================================================


def _check_native_available() -> bool:
    """Check if cleon._native is available for OAuth operations."""
    try:
        from cleon import _native  # noqa: F401

        return True
    except ImportError:
        return False


def login_claude_pimono() -> None:
    """Interactive OAuth login for Claude Pro/Max using cleon._native.

    This function uses the cleon._native Rust bindings for OAuth, providing consistent
    auth handling across all providers.
    """
    try:
        from cleon._native import (
            AuthStorage,
            anthropic_exchange_code,
            anthropic_get_auth_url,
            get_agent_dir,
        )
    except ImportError:
        print(
            "cleon._native not available. Build cleon with maturin: cd python && maturin develop"
        )
        return

    # Get auth URL and verifier
    auth_url, verifier = anthropic_get_auth_url()
    print("Open the following URL in your browser and authorize cleon:\n")
    print(auth_url)
    print(
        '\nAfter authorizing, copy the complete "code#state" value and paste it below.'
    )
    code_input = input("Authorization response (code#state): ").strip()
    if not code_input:
        print("Login cancelled.")
        return
    if "#" not in code_input:
        print("Invalid response. Expected format: <code>#<state>")
        return
    code, _ = code_input.split("#", 1)

    # Exchange code for credentials
    try:
        creds = anthropic_exchange_code(code, verifier)
    except RuntimeError as e:
        print(f"OAuth token exchange failed: {e}")
        print("If this keeps failing, set ANTHROPIC_API_KEY directly to skip OAuth.")
        return

    # Store credentials using pi-mono-rust AuthStorage
    auth_path = Path(get_agent_dir()) / "auth.json"
    auth = AuthStorage(str(auth_path))
    auth.set(
        "anthropic",
        {
            "type": "oauth",
            "access": creds.get("access"),
            "refresh": creds.get("refresh"),
            "expires": creds.get("expires"),
        },
    )
    print(f"Claude login successful. Tokens stored in {auth_path}")
    _refresh_active_claude_backend()


def login_codex_pimono() -> None:
    """Interactive OAuth login for OpenAI Codex using cleon._native.

    This function uses the cleon._native Rust bindings for OAuth, providing consistent
    auth handling across all providers.
    """
    try:
        from cleon._native import (
            AuthStorage,
            get_agent_dir,
            openai_codex_exchange_code,
            openai_codex_get_auth_url,
        )
    except ImportError:
        print(
            "cleon._native not available. Build cleon with maturin: cd python && maturin develop"
        )
        return

    # Get auth URL, verifier, and state
    auth_url, verifier, state = openai_codex_get_auth_url()
    print("Open the following URL in your browser and authorize cleon:\n")
    print(auth_url)
    print(
        "\nAfter authorizing, copy the complete authorization code and paste it below."
    )
    print(f"(Expected state for verification: {state[:8]}...)")
    code_input = input("Authorization code: ").strip()
    if not code_input:
        print("Login cancelled.")
        return

    # Exchange code for credentials
    try:
        creds = openai_codex_exchange_code(code_input, verifier)
    except RuntimeError as e:
        print(f"OAuth token exchange failed: {e}")
        print("If this keeps failing, set OPENAI_API_KEY directly to skip OAuth.")
        return

    # Store credentials using pi-mono-rust AuthStorage
    auth_path = Path(get_agent_dir()) / "auth.json"
    auth = AuthStorage(str(auth_path))
    auth.set(
        "openai-codex",
        {
            "type": "oauth",
            "access": creds.get("access"),
            "refresh": creds.get("refresh"),
            "expires": creds.get("expires"),
        },
    )
    print(f"Codex login successful. Tokens stored in {auth_path}")
    _refresh_active_codex_backend()


def login_pimono(provider: str = "claude") -> None:
    """Unified OAuth login using cleon._native.

    Args:
        provider: "claude" (or "anthropic") for Claude, "codex" for OpenAI Codex
    """
    provider = provider.lower()
    if provider in {"claude", "anthropic", "pi"}:
        return login_claude_pimono()
    elif provider in {"codex", "openai", "openai-codex"}:
        return login_codex_pimono()
    else:
        raise ValueError(f"Unknown provider '{provider}'. Supported: claude, codex")
