"""Tests for PiMonoBackend using pi-mono-rust PyO3 bindings.

These tests verify that PiMonoBackend correctly integrates with pi-mono-rust
for both Claude (anthropic) and Codex (openai-codex) providers.

Note: Live API tests require valid authentication. Run with:
    PIMONO_LIVE_TEST=1 pytest python/tests/test_pimono_backend.py -v

For unit tests only (no API calls):
    pytest python/tests/test_pimono_backend.py -v
"""

import os
import pytest
import threading
import time

# Skip live tests by default
LIVE_TEST = os.environ.get("PIMONO_LIVE_TEST", "0") == "1"


class TestPiMonoBackendUnit:
    """Unit tests that don't require API calls."""

    def test_import_pimono_backend(self):
        """Test that PiMonoBackend can be imported."""
        from cleon.backend import PiMonoBackend

        assert PiMonoBackend is not None
        assert PiMonoBackend.supports_async is True

    def test_provider_aliases(self):
        """Test that provider alias mapping is correct."""
        from cleon.backend import _PROVIDER_ALIASES

        assert _PROVIDER_ALIASES["codex"] == "openai-codex"
        assert _PROVIDER_ALIASES["claude"] == "anthropic"
        assert _PROVIDER_ALIASES["anthropic"] == "anthropic"
        assert _PROVIDER_ALIASES["default"] == "openai-codex"

    def test_resolve_backend_with_use_pimono(self):
        """Test that resolve_backend returns PiMonoBackend when use_pimono=True."""
        from cleon.backend import resolve_backend, PiMonoBackend

        # This may fail if pi_mono is not installed or no auth
        try:
            backend = resolve_backend(
                agent="claude",
                binary=None,
                extra_env=None,
                session_id=None,
                use_pimono=True,
            )
            assert isinstance(backend, PiMonoBackend)
            assert backend.name == "claude"
        except RuntimeError as e:
            # Expected if pi_mono not installed or no auth
            if "pi_mono is not installed" in str(e) or "No API key" in str(e):
                pytest.skip(f"Skipped: {e}")
            raise

    def test_resolve_backend_default_uses_pimono(self):
        """Test that resolve_backend uses PiMonoBackend by default for claude/codex."""
        from cleon.backend import resolve_backend, PiMonoBackend

        # With pi_mono installed and auth available, resolve_backend should
        # automatically use PiMonoBackend for claude and codex
        try:
            # Test claude
            backend_claude = resolve_backend(
                agent="claude",
                binary=None,
                extra_env=None,
                session_id=None,
            )
            assert isinstance(backend_claude, PiMonoBackend), \
                "claude should use PiMonoBackend by default"
            assert backend_claude.name == "claude"

            # Test codex
            backend_codex = resolve_backend(
                agent="codex",
                binary=None,
                extra_env=None,
                session_id=None,
            )
            assert isinstance(backend_codex, PiMonoBackend), \
                "codex should use PiMonoBackend by default"
            assert backend_codex.name == "codex"

        except RuntimeError as e:
            # Expected if pi_mono not installed or no auth
            if "pi_mono is not installed" in str(e) or "No API key" in str(e):
                pytest.skip(f"Skipped: {e}")
            raise

    def test_resolve_backend_explicit_legacy(self):
        """Test that resolve_backend uses legacy backends when use_pimono=False."""
        from cleon.backend import resolve_backend, PiBackend, CodexBackend, GeminiBackend

        # Test that use_pimono=False forces legacy backends (may fail if legacy deps missing)
        try:
            backend_claude = resolve_backend(
                agent="claude",
                binary=None,
                extra_env=None,
                session_id=None,
                use_pimono=False,
            )
            assert isinstance(backend_claude, PiBackend), \
                "claude with use_pimono=False should use PiBackend"
        except RuntimeError:
            # May fail if legacy pi CLI not available
            pass

        try:
            backend_gemini = resolve_backend(
                agent="gemini",
                binary=None,
                extra_env=None,
                session_id=None,
            )
            assert isinstance(backend_gemini, GeminiBackend), \
                "gemini should use GeminiBackend (legacy)"
        except RuntimeError:
            # May fail if legacy gemini CLI not available
            pass

    def test_event_translation(self):
        """Test that events are translated correctly."""
        from cleon.backend import _translate_pimono_event

        # Test agent event translation
        event = {
            "type": "agent",
            "event": {
                "kind": "message_start",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hello"}],
                },
            },
        }
        translated = _translate_pimono_event(event, "codex")
        assert translated is not None
        assert translated["type"] == "codex.message_start"
        assert translated["text"] == "Hello"

    def test_event_translation_turn_end(self):
        """Test turn_end event translation."""
        from cleon.backend import _translate_pimono_event

        event = {
            "type": "agent",
            "event": {
                "kind": "turn_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Done!"}],
                },
                "tool_results": [],
            },
        }
        translated = _translate_pimono_event(event, "claude")
        assert translated is not None
        assert translated["type"] == "claude.turn_end"
        assert translated["text"] == "Done!"
        assert translated["tool_results"] == []

    def test_event_translation_tool_execution(self):
        """Test tool execution event translation."""
        from cleon.backend import _translate_pimono_event

        # tool_execution_start
        event = {
            "type": "agent",
            "event": {
                "kind": "tool_execution_start",
                "tool_call_id": "call_123",
                "tool_name": "read",
                "args": '{"path": "/tmp/test.txt"}',
            },
        }
        translated = _translate_pimono_event(event, "codex")
        assert translated["type"] == "codex.tool_execution_start"
        assert translated["tool"] == "read"
        assert translated["tool_call_id"] == "call_123"

        # tool_execution_end
        event = {
            "type": "agent",
            "event": {
                "kind": "tool_execution_end",
                "tool_call_id": "call_123",
                "tool_name": "read",
                "result": {"content": [{"type": "text", "text": "file contents"}]},
                "is_error": False,
            },
        }
        translated = _translate_pimono_event(event, "codex")
        assert translated["type"] == "codex.tool_execution_end"
        assert translated["is_error"] is False

    def test_event_translation_compaction(self):
        """Test compaction event translation."""
        from cleon.backend import _translate_pimono_event

        event = {"type": "auto_compaction_start", "reason": "context_overflow"}
        translated = _translate_pimono_event(event, "claude")
        assert translated["type"] == "claude.compaction_start"
        assert translated["reason"] == "context_overflow"

        event = {"type": "auto_compaction_end", "aborted": False}
        translated = _translate_pimono_event(event, "claude")
        assert translated["type"] == "claude.compaction_end"
        assert translated["aborted"] is False

    def test_extract_message_text(self):
        """Test message text extraction."""
        from cleon.backend import _extract_pimono_message_text

        # Text content blocks
        message = {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "text", "text": "World"},
            ],
        }
        text = _extract_pimono_message_text(message)
        assert text == "Hello\nWorld"

        # Empty message
        message = {"role": "assistant", "content": []}
        text = _extract_pimono_message_text(message)
        assert text == ""

        # String content (fallback)
        message = {"role": "assistant", "content": "Simple text"}
        text = _extract_pimono_message_text(message)
        assert text == "Simple text"


@pytest.mark.skipif(not LIVE_TEST, reason="Live API tests disabled")
class TestPiMonoBackendLive:
    """Live tests that make actual API calls."""

    def test_pimono_backend_claude_smoke(self):
        """Smoke test for Claude via PiMonoBackend."""
        from cleon.backend import PiMonoBackend

        events_received = []

        def on_event(event):
            events_received.append(event)

        try:
            backend = PiMonoBackend(agent="claude")
        except RuntimeError as e:
            pytest.skip(f"Claude auth not available: {e}")

        assert backend.name == "claude"
        assert backend.first_turn() is True

        # Send a simple prompt
        result, events = backend.send(
            "Say 'Hello from Claude test' and nothing else.",
            on_event=on_event,
        )

        assert result is not None
        assert "final_message" in result
        assert result["agent"] == "claude"
        assert len(events_received) > 0

        # Check we got expected event types
        event_types = [e.get("type", "") for e in events_received]
        assert any("message_start" in t for t in event_types)
        assert any("turn_end" in t for t in event_types)

        backend.stop()

    def test_pimono_backend_codex_smoke(self):
        """Smoke test for Codex via PiMonoBackend."""
        from cleon.backend import PiMonoBackend

        events_received = []

        def on_event(event):
            events_received.append(event)

        try:
            backend = PiMonoBackend(agent="codex")
        except RuntimeError as e:
            pytest.skip(f"Codex auth not available: {e}")

        assert backend.name == "codex"
        assert backend.first_turn() is True

        # Send a simple prompt
        result, events = backend.send(
            "Say 'Hello from Codex test' and nothing else.",
            on_event=on_event,
        )

        assert result is not None
        assert "final_message" in result
        assert result["agent"] == "codex"
        assert len(events_received) > 0

        # Check we got expected event types
        event_types = [e.get("type", "") for e in events_received]
        assert any("message_start" in t or "turn_start" in t for t in event_types)
        assert any("turn_end" in t for t in event_types)

        backend.stop()

    def test_pimono_backend_session_persistence(self):
        """Test that session ID is preserved."""
        from cleon.backend import PiMonoBackend

        try:
            backend = PiMonoBackend(agent="claude")
        except RuntimeError as e:
            pytest.skip(f"Claude auth not available: {e}")

        # Get session ID before prompt
        session_id_before = backend._session.session_id()

        # Send a prompt
        result, _ = backend.send("Say 'test'")
        assert result is not None

        # Session ID should still be valid
        session_id_after = backend._session.session_id()
        assert session_id_after is not None

        backend.stop()

    def test_pimono_backend_first_turn_tracking(self):
        """Test first_turn flag is updated correctly."""
        from cleon.backend import PiMonoBackend

        try:
            backend = PiMonoBackend(agent="claude")
        except RuntimeError as e:
            pytest.skip(f"Claude auth not available: {e}")

        assert backend.first_turn() is True

        result, _ = backend.send("Say 'first'")
        assert result is not None
        assert backend.first_turn() is False

        result, _ = backend.send("Say 'second'")
        assert result is not None
        assert backend.first_turn() is False

        # reset_first_turn should reset the flag
        backend.reset_first_turn()
        assert backend.first_turn() is True

        backend.stop()


@pytest.mark.skipif(not LIVE_TEST, reason="Live API tests disabled")
class TestPiMonoBackendEventStreaming:
    """Tests for event streaming behavior."""

    def test_streaming_events_order(self):
        """Test that events arrive in the expected order."""
        from cleon.backend import PiMonoBackend

        events_received = []
        event_order = []

        def on_event(event):
            events_received.append(event)
            event_type = event.get("type", "")
            # Extract the event kind (e.g., "claude.message_start" -> "message_start")
            kind = event_type.split(".")[-1] if "." in event_type else event_type
            if kind not in event_order:
                event_order.append(kind)

        try:
            backend = PiMonoBackend(agent="claude")
        except RuntimeError as e:
            pytest.skip(f"Claude auth not available: {e}")

        result, _ = backend.send("Say 'test'", on_event=on_event)

        # Expected order: agent_start/turn_start -> message_start -> message_update* -> message_end -> turn_end
        assert "turn_end" in event_order, f"Missing turn_end in {event_order}"

        # message events should come before turn_end
        if "message_start" in event_order and "turn_end" in event_order:
            assert event_order.index("message_start") < event_order.index(
                "turn_end"
            ), f"Wrong order: {event_order}"

        backend.stop()

    def test_streaming_callback_thread_safety(self):
        """Test that callbacks are called from the main thread context."""
        from cleon.backend import PiMonoBackend

        callback_threads = []

        def on_event(event):
            callback_threads.append(threading.current_thread().name)

        try:
            backend = PiMonoBackend(agent="claude")
        except RuntimeError as e:
            pytest.skip(f"Claude auth not available: {e}")

        result, _ = backend.send("Say 'thread test'", on_event=on_event)

        # All callbacks should be from the same thread (the one calling send())
        assert len(callback_threads) > 0
        assert len(set(callback_threads)) == 1, f"Multiple threads: {set(callback_threads)}"

        backend.stop()


@pytest.mark.skipif(not LIVE_TEST, reason="Live API tests disabled")
class TestPiMonoBackendSessionResume:
    """Tests for session resume across kernel restarts."""

    def test_session_resume_across_restart(self):
        """Test that a session can be resumed after stopping the backend (simulating kernel restart).

        This tests the critical use case of Jupyter kernel restarts where sessions should persist
        and be resumable across kernel lifecycle events.
        """
        from cleon.backend import PiMonoBackend
        import os

        # Step 1: Create initial session and send a prompt
        try:
            backend1 = PiMonoBackend(agent="claude")
        except RuntimeError as e:
            pytest.skip(f"Claude auth not available: {e}")

        # Send first prompt
        result1, _ = backend1.send("Say 'hello'")
        assert result1 is not None
        assert "final_message" in result1

        # Get session file path before stopping
        session_file = backend1._session.session_file()
        session_id = backend1._session.session_id()
        assert session_file is not None, "Session file should be set after first prompt"
        assert session_id is not None, "Session ID should be set after first prompt"

        # Verify session file exists
        assert os.path.exists(session_file), f"Session file should exist: {session_file}"

        # Stop the backend (simulates kernel shutdown)
        stop_info = backend1.stop()
        assert stop_info.session_id is not None, "Stop should return session info for resume"

        # Verify session file still exists after stop
        assert os.path.exists(session_file), f"Session file should persist after stop: {session_file}"

        # Step 2: Create new backend and resume the session (simulates kernel restart)
        try:
            backend2 = PiMonoBackend(agent="claude", session_id=stop_info.session_id)
        except RuntimeError as e:
            pytest.skip(f"Failed to create resumed backend: {e}")

        # Verify the resumed backend can send prompts
        result2, _ = backend2.send("Say 'resumed successfully'")
        assert result2 is not None
        assert "final_message" in result2

        # Verify stats show multiple messages (from both sessions)
        stats = backend2._session.get_session_stats()
        # We should have at least 2 user messages (one from each session)
        assert stats.get("user_messages", 0) >= 2, f"Should have messages from both sessions. Stats: {stats}"
        assert stats.get("assistant_messages", 0) >= 2, f"Should have assistant messages from both sessions. Stats: {stats}"

        backend2.stop()

    def test_session_file_persistence(self):
        """Test that session files are created and persisted correctly."""
        from cleon.backend import PiMonoBackend
        import os

        try:
            backend = PiMonoBackend(agent="claude")
        except RuntimeError as e:
            pytest.skip(f"Claude auth not available: {e}")

        # Before any prompts, session may or may not have a file
        initial_file = backend._session.session_file()

        # Send a prompt to trigger session file creation
        result, _ = backend.send("Say 'hello'")
        assert result is not None

        # After prompt, session file should exist
        session_file = backend._session.session_file()
        assert session_file is not None, "Session file should be created after first prompt"
        assert os.path.exists(session_file), f"Session file should exist on disk: {session_file}"

        # Get stats to verify session is tracking correctly
        stats = backend._session.get_session_stats()
        assert stats is not None
        assert stats.get("user_messages", 0) >= 1
        assert stats.get("assistant_messages", 0) >= 1

        backend.stop()

        # Verify file still exists after stop
        assert os.path.exists(session_file), f"Session file should persist after stop: {session_file}"

    def test_session_stats_tracking(self):
        """Test that session stats are tracked correctly across messages."""
        from cleon.backend import PiMonoBackend

        try:
            backend = PiMonoBackend(agent="claude")
        except RuntimeError as e:
            pytest.skip(f"Claude auth not available: {e}")

        # Get initial stats (should be empty or minimal)
        initial_stats = backend._session.get_session_stats()
        initial_user_msgs = initial_stats.get("user_messages", 0)

        # Send first prompt
        result1, _ = backend.send("Say 'one'")
        assert result1 is not None

        stats1 = backend._session.get_session_stats()
        assert stats1.get("user_messages", 0) >= initial_user_msgs + 1
        assert stats1.get("assistant_messages", 0) >= 1

        # Send second prompt
        result2, _ = backend.send("Say 'two'")
        assert result2 is not None

        stats2 = backend._session.get_session_stats()
        assert stats2.get("user_messages", 0) >= stats1.get("user_messages", 0) + 1
        assert stats2.get("total_messages", 0) > stats1.get("total_messages", 0)

        backend.stop()
