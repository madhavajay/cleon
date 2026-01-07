"""Backend abstractions for cleon agents.

All providers (Codex, Claude, Gemini) now use the unified PiMonoBackend
which wraps pi-mono-rust's AgentSession via PyO3 bindings.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol


class AgentBackend(Protocol):
    """Interface implemented by concrete agent backends."""

    name: str
    supports_async: bool

    def first_turn(self) -> bool: ...

    def send(
        self,
        prompt: str,
        *,
        on_event: Callable[[Any], None] | None = None,
        on_approval: Callable[[dict[str, Any]], str | None] | None = None,
    ) -> tuple[Any, list[Any]]: ...

    def run_once(self, prompt: str) -> tuple[Any, list[Any]]: ...

    def stop(self) -> "SessionStopInfo": ...

    def session_alive(self) -> bool: ...


@dataclass
class SessionStopInfo:
    """Metadata captured when a backend session stops."""

    session_id: str | None
    resume_command: str | None


# ============================================================================
# Unified PiMonoBackend using pi-mono-rust PyO3 bindings
# ============================================================================

# Map provider aliases to canonical provider names
_PROVIDER_ALIASES: dict[str, str] = {
    "codex": "openai-codex",
    "default": "openai-codex",
    "claude": "anthropic",
    "anthropic": "anthropic",
    "gemini": "google-gemini-cli",
    "google": "google-gemini-cli",
    "google-gemini-cli": "google-gemini-cli",
}


def _translate_pimono_event(
    event: dict[str, Any], agent_name: str
) -> dict[str, Any] | None:
    """Translate pi-mono-rust AgentSessionEvent to cleon event format.

    pi-mono-rust events come as:
        {type: "agent", event: {kind: "...", ...}}
        {type: "auto_compaction_start", reason: "..."}
        {type: "auto_compaction_end", aborted: bool}

    We translate to cleon format:
        {type: "{agent}.{kind}", ...fields}
    """
    event_type = event.get("type")

    if event_type == "agent":
        inner = event.get("event", {})
        kind = inner.get("kind")
        if not isinstance(kind, str):
            return {"type": f"{agent_name}.event", "event": event}

        payload: dict[str, Any] = {"type": f"{agent_name}.{kind}"}

        # Handle message events
        if kind in {"message_start", "message_update", "message_end"}:
            message = inner.get("message", {})
            payload["text"] = _extract_pimono_message_text(message)
            payload["raw"] = message

        # Handle turn_end
        elif kind == "turn_end":
            message = inner.get("message", {})
            payload["text"] = _extract_pimono_message_text(message)
            payload["raw"] = message
            payload["tool_results"] = inner.get("tool_results", [])

        # Handle tool execution events
        elif kind == "tool_execution_start":
            payload.update(
                {
                    "tool": inner.get("tool_name"),
                    "args": inner.get("args"),
                    "tool_call_id": inner.get("tool_call_id"),
                }
            )
        elif kind == "tool_execution_update":
            payload.update(
                {
                    "tool": inner.get("tool_name"),
                    "args": inner.get("args"),
                    "tool_call_id": inner.get("tool_call_id"),
                    "partial_result": inner.get("partial_result"),
                }
            )
        elif kind == "tool_execution_end":
            payload.update(
                {
                    "tool": inner.get("tool_name"),
                    "result": inner.get("result"),
                    "is_error": inner.get("is_error"),
                    "tool_call_id": inner.get("tool_call_id"),
                }
            )

        # Handle agent/turn lifecycle events
        elif kind in {"agent_start", "agent_end", "turn_start"}:
            payload.update({k: v for k, v in inner.items() if k != "kind"})

        return payload

    elif event_type == "auto_compaction_start":
        return {
            "type": f"{agent_name}.compaction_start",
            "reason": event.get("reason"),
        }
    elif event_type == "auto_compaction_end":
        return {
            "type": f"{agent_name}.compaction_end",
            "aborted": event.get("aborted"),
        }

    # Unknown event type
    return {"type": f"{agent_name}.event", "event": event}


def _extract_pimono_message_text(message: Any) -> str:
    """Extract text from pi-mono-rust message structure.

    Message format:
        {role: "assistant", content: [{type: "text", text: "..."}, ...]}
    """
    if not isinstance(message, dict):
        return ""
    parts: list[str] = []
    content = message.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
    elif isinstance(content, str):
        parts.append(content)
    return "\n".join(
        part.strip() for part in parts if isinstance(part, str) and part.strip()
    )


class PiMonoBackend:
    """Unified backend using pi-mono-rust PyO3 bindings.

    This backend supports all providers (Codex, Claude, Gemini) through the
    pi-mono-rust library instead of spawning separate CLI processes.
    """

    supports_async = True

    def __init__(
        self,
        *,
        agent: str = "codex",
        provider: str | None = None,
        model: str | None = None,
        extra_env: Mapping[str, str] | None = None,
        session_id: str | None = None,
    ) -> None:
        # Lazy import to avoid hard dependency on pi_mono at module load
        try:
            from pi_mono import AgentSession, get_agent_dir
        except ImportError as exc:
            raise RuntimeError(
                "pi_mono is not installed. "
                "Build and install it from pi-mono-rust: maturin develop --features python"
            ) from exc

        self._agent = agent.lower()
        self.name = self._agent

        # Resolve provider from agent name if not explicitly set
        if provider is None:
            provider = _PROVIDER_ALIASES.get(self._agent, "anthropic")
        self._provider = provider
        self._model = model

        # Apply extra env vars
        if extra_env:
            for key, value in extra_env.items():
                os.environ[str(key)] = str(value)

        # Create session
        cwd = os.getcwd()
        agent_dir = get_agent_dir()

        try:
            self._session = AgentSession(
                cwd=cwd,
                agent_dir=agent_dir,
                provider=provider,
                model=model,
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to create AgentSession: {exc}") from exc

        self._first_turn = True
        self._send_lock = threading.Lock()
        self._session_id = session_id

        # Resume session if session_id provided
        if session_id:
            try:
                self._session.switch_session(session_id)
            except Exception:
                # If resume fails, continue with new session
                pass

        # Event collection for current prompt
        self._current_events: list[dict[str, Any]] = []
        self._current_on_event: Callable[[Any], None] | None = None
        self._turn_completed = threading.Event()
        self._final_message: str | None = None

        # Subscribe to session events
        self._session.subscribe(self._handle_event)

    def _handle_event(self, event: dict[str, Any]) -> None:
        """Internal event handler for pi-mono-rust events."""
        translated = _translate_pimono_event(event, self._agent)
        if translated is None:
            return

        self._current_events.append(translated)

        # Forward to external callback if set
        if self._current_on_event:
            try:
                self._current_on_event(translated)
            except Exception:
                pass

        # Check for turn_end to complete the send() call
        event_type = event.get("type")
        if event_type == "agent":
            inner = event.get("event", {})
            kind = inner.get("kind")
            if kind == "turn_end":
                # Extract final message text
                message = inner.get("message", {})
                self._final_message = _extract_pimono_message_text(message)
                self._turn_completed.set()

    def first_turn(self) -> bool:
        return self._first_turn

    def reset_first_turn(self) -> None:
        with self._send_lock:
            self._first_turn = True

    def send(
        self,
        prompt: str,
        *,
        on_event: Callable[[Any], None] | None = None,
        on_approval: Callable[[dict[str, Any]], str | None] | None = None,
    ) -> tuple[Any, list[Any]]:
        with self._send_lock:
            # Reset state for this turn
            self._current_events = []
            self._current_on_event = on_event
            self._turn_completed.clear()
            self._final_message = None

            # Set up approval callback if provided
            if on_approval is not None:
                # Wrap the cleon approval callback in the format pi-mono-rust expects.
                # cleon's on_approval returns: "approve", "approve_session", "deny", "abort"
                # pi-mono-rust expects the same strings.
                def approval_wrapper(request: dict[str, Any]) -> str:
                    result = on_approval(request)
                    # Ensure we return a valid string
                    if result in ("approve", "approve_session", "deny", "abort"):
                        return result
                    # Default to approve if unexpected return value
                    return "approve"

                self._session.set_approval_callback(approval_wrapper)
            else:
                # Clear any previous approval callback
                self._session.set_approval_callback(None)

            try:
                # Send the prompt
                self._session.prompt(prompt)
                self._first_turn = False

                # Wait for turn_end event (with timeout)
                timeout = 300.0  # 5 minutes
                if not self._turn_completed.wait(timeout=timeout):
                    raise RuntimeError("Timed out waiting for response.")

                # Build result
                final_text = self._final_message or ""
                if not final_text:
                    final_text = self._session.get_last_assistant_text() or ""
                if not final_text:
                    final_text = "Response completed."

                result = {"final_message": final_text, "agent": self._agent}
                return result, list(self._current_events)

            finally:
                self._current_on_event = None
                # Clear approval callback after prompt completes
                self._session.set_approval_callback(None)

    def run_once(self, prompt: str) -> tuple[Any, list[Any]]:
        """Execute a single-shot prompt (fresh session)."""
        self._session.new_session()
        self._first_turn = True
        return self.send(prompt)

    def stop(self) -> SessionStopInfo:
        """Stop the session and return resume info."""
        session_id = self._session.session_id()
        session_file = self._session.session_file()
        self._session.dispose()
        return SessionStopInfo(
            session_id=session_file or session_id,
            resume_command=None,  # pi-mono-rust doesn't use CLI commands
        )

    def session_alive(self) -> bool:
        """Check if the session is still active."""
        # pi-mono-rust sessions are always "alive" until disposed
        return self._session is not None and self._session.session_id() is not None

    def restart(self) -> None:
        """Restart with a fresh session."""
        with self._send_lock:
            self._session.new_session()
            self._first_turn = True


def resolve_backend(
    *,
    agent: str,
    binary: str | None = None,
    extra_env: Mapping[str, str] | None = None,
    session_id: str | None = None,
) -> AgentBackend:
    """Resolve which backend to use for the given agent.

    All agents now use PiMonoBackend which wraps pi-mono-rust.

    Args:
        agent: Agent name (codex, claude, gemini, etc.)
        binary: Ignored (legacy parameter)
        extra_env: Extra environment variables
        session_id: Session ID to resume

    Returns:
        A PiMonoBackend instance.
    """
    del binary  # No longer used

    agent_name = agent.lower()

    # All agents use PiMonoBackend
    return PiMonoBackend(
        agent=agent_name,
        extra_env=extra_env,
        session_id=session_id,
    )
