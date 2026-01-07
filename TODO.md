# TODO

## Current Status: Live Testing PASSED for Claude, Codex, and Session Resume

### Summary (2026-01-07)

**Phase 2 Python integration is VALIDATED:**
- PiMonoBackend class implemented in backend.py with event translation
- magic.py updated with `use_pimono` flag passthrough for `use()`, `register_magic()`, `resume()`
- cleon.auth() updated to support pi-mono-rust OAuth via `use_pimono=True`
- **All 17 tests pass (8 unit + 9 live API tests)**

**Live API Testing Results:**
- ✅ Claude (anthropic) provider: Live test passed
- ✅ Codex (openai-codex) provider: Live test passed
- ✅ Session resume across kernel restarts: 3 new tests pass
- ⏸️ Gemini (google-gemini-cli) provider: PyO3 support added, token expired - needs re-auth

**PyO3 bindings:**
- PyO3 bindings are located in `pi-mono-rust/src/python/mod.rs`
- If you need to change anything in pi-mono-rust, make sure to create a branch for it
- Added `build_gemini_stream_fn()` to PyO3 bindings for google-gemini-cli API
- PyO3 bindings built and installed via `maturin develop --features python`
- `pi_mono` Python module imports and works correctly
- Clippy passes, lib tests pass

**Session Resume Validated:**
- Session files persist correctly at `~/.pi/agent/sessions/`
- Session resume via `switch_session(path)` works across backend restarts
- Session stats are tracked correctly across messages
- 3 new tests added: `test_session_resume_across_restart`, `test_session_file_persistence`, `test_session_stats_tracking`

### Next Steps
1. ~~Run live API tests with Claude and Codex providers~~ ✅ DONE
2. ~~Test Gemini provider via PiMonoBackend~~ ⏸️ Token expired, code ready
3. ~~Validate session resume across kernel restarts~~ ✅ DONE
4. ~~Enable PiMonoBackend by default (remove use_pimono flag requirement)~~ ✅ DONE
5. Clean up legacy backends once PiMonoBackend is stable

### Build Instructions
```bash
# Build pi_mono Python module (requires Python 3.13 due to PyO3 compatibility)
cd pi-mono-rust
source .venv/bin/activate  # Python 3.13 venv
maturin develop --features python

# Install cleon package
pip install -e python/

# Run unit tests
python -m pytest python/tests/test_pimono_backend.py -v

# Run live tests (requires PIMONO_LIVE_TEST=1 and valid auth)
PIMONO_LIVE_TEST=1 python -m pytest python/tests/test_pimono_backend.py -v
```

### Testing pi CLI Locally
```bash
# Test with Claude (Anthropic)
cd pi-mono-rust && ./pi --provider anthropic --model claude-opus-4-5 --mode json -p "who are you"

# Test with Codex (OpenAI)
cd pi-mono-rust && ./pi --provider openai-codex --model gpt-5.2-codex --mode json -p "who are you"
```

---

## Phase 1: PyO3 Bindings (COMPLETE)

### 1.1 Create PyO3 module in pi-mono-rust
- [x] Review pi-mono-rust APIs for sessions, streaming events, auth/login, and provider config
- [x] Add `pyo3` dependency to pi-mono-rust `Cargo.toml` with `extension-module` feature
- [x] Create PyO3 module (`src/python/mod.rs`) that wraps pi-mono-rust types
- [x] Export core types to Python:
  - [x] `PyAgentSession` wrapper around `AgentSession`
  - [x] `PyAuthStorage` wrapper around `AuthStorage`

### 1.2 Expose minimal API surface for Cleon
Required methods (matching AgentBackend protocol):
- [x] `session.prompt(text: str) -> None` - Send prompt
- [x] `session.subscribe(callback: Callable) -> int` - Event subscription
- [x] `session.is_streaming() -> bool` - Check streaming state
- [x] `session.abort() -> None` - Cancel current operation
- [x] `session.new_session() -> None` - Start fresh session
- [x] `session.switch_session(path: str) -> bool` - Resume session
- [x] `session.session_id() -> Optional[str]` - Get current session ID
- [x] `session.session_file() -> Optional[str]` - Get session file path
- [x] `session.get_session_stats() -> dict` - Get session statistics
- [x] `session.get_last_assistant_text() -> Optional[str]` - Get last response
- [x] `session.dispose() -> None` - Clean up session

### 1.3 Expose auth methods
- [x] `auth.get(provider: str) -> Optional[dict]`
- [x] `auth.set(provider: str, credential: dict) -> None`
- [x] `auth.remove(provider: str) -> None`
- [x] `auth.has_auth(provider: str) -> bool`
- [x] `auth.get_api_key(provider: str) -> Optional[str]`
- [x] `auth.list() -> List[str]`
- [x] `auth.reload() -> None`
- [x] `anthropic_get_auth_url() -> (str, str)` - returns (url, verifier)
- [x] `anthropic_exchange_code(code: str, verifier: str) -> dict`
- [x] `anthropic_refresh_token(refresh_token: str) -> dict`
- [x] `openai_codex_get_auth_url() -> (str, str, str)` - returns (url, verifier, state)
- [x] `openai_codex_exchange_code(code: str, verifier: str) -> dict`
- [x] `openai_codex_refresh_token(refresh_token: str) -> dict`
- [x] `get_agent_dir() -> str`

### 1.4 Expose event types as Python dicts
- [x] `AgentSessionEvent` → dict with `type` field
- [x] `AgentEvent` → dict with `kind` and event-specific fields
- [x] `AgentMessage` → dict with `role` and content fields
- [x] `ContentBlock` → dict with `type` and content fields

---

## Phase 1.5: Build & Test Python Extension (COMPLETE)

### 1.5.1 Build Python extension module
- [x] Set up maturin with pyproject.toml for building the extension
- [x] Build `_pi_mono` Python module from pi-mono-rust
- [x] Test basic import in Python: `import pi_mono`
- [x] Verify `PyAuthStorage` instantiation and method calls
- [x] Verify `PyAgentSession` instantiation with test cwd

### 1.5.2 Create Python wrapper package
- [x] Create `python/pi_mono/__init__.py` with high-level Python API
- [ ] Add type stubs for IDE support (low priority)
- [x] Platform-specific library loading works via maturin

---

## Phase 2: Replace Cleon Backends (IN PROGRESS)

### 2.1 Create unified PiMonoBackend class
- [x] Create `PiMonoBackend` class in backend.py
- [x] Implement AgentBackend protocol:
  - [x] `name` property
  - [x] `supports_async` property
  - [x] `first_turn()` method
  - [x] `reset_first_turn()` method
  - [x] `send()` with event callbacks
  - [x] `run_once()` for single-shot execution
  - [x] `stop()` returning SessionStopInfo
  - [x] `session_alive()` method
  - [x] `restart()` method
- [x] Wire up PyO3 API streaming functions
- [x] Test with Claude provider via PiMonoBackend ✓
- [x] Test with Codex provider via PiMonoBackend ✓

### 2.2 Update resolve_backend() function
- [x] Added `use_pimono` parameter to resolve_backend()
- [ ] Enable PiMonoBackend for Claude by default (after validation)
- [ ] Enable PiMonoBackend for Codex by default (after validation)
- [ ] Enable PiMonoBackend for Gemini by default (after validation)

### 2.3 Translate events to Cleon format
- [x] Created `_translate_pimono_event()` function
- [x] Map `AgentEvent` types to existing event format:
  - [x] `agent_start` → `{agent}.agent_start`
  - [x] `turn_start` → `{agent}.turn_start`
  - [x] `message_start/update/end` → `{agent}.message_start/update/end`
  - [x] `tool_execution_start/update/end` → `{agent}.tool_execution_start/update/end`
  - [x] `turn_end` → `{agent}.turn_end`
- [x] Created `_extract_pimono_message_text()` helper

---

## Phase 3: Session & Auth Unification

### 3.1 Unify session handling
- [x] PiMonoBackend uses pi-mono-rust SessionManager
- [x] Session files stored in `~/.pi/agent/sessions/`
- [x] Session resume via `switch_session(path)`
- [ ] Update cleon.resume() to use PiMonoBackend sessions
- [ ] Test session continuity across restart

### 3.2 Unify auth storage
- [ ] Remove separate oauth.py Claude OAuth handling (after testing)
- [x] Use pi-mono-rust AuthStorage for all providers ✓
- [x] Auth file: `~/.pi/agent/auth.json` ✓
- [x] Update cleon.auth() to use PyO3 bindings ✓
  - Added `use_pimono` parameter to `auth()` and `login()` functions
  - Added `login_pimono()`, `login_claude_pimono()`, `login_codex_pimono()` in oauth.py

### 3.3 Update magic.py
- [x] Add use_pimono flag passthrough from magic commands ✓
- [ ] Remove provider-specific code paths (after validation)
- [ ] Keep template/context system working

---

## Phase 4: Cleanup & Documentation

### 4.1 Remove deprecated code
- [ ] Delete `SharedSession` class (Codex CLI wrapper)
- [ ] Delete `PiProcess` class (Pi CLI wrapper)
- [ ] Delete `GeminiProcess` class (Gemini CLI wrapper)
- [ ] Delete CLI resolution functions
- [ ] Remove legacy Codex Rust dependency: drop `codex-*` crates from `Cargo.toml`
- [ ] Remove Codex-specific CLI binaries (`src/main.rs`) or rewire them to pi-mono-rust only

### 4.2 Update documentation
- [ ] Update README with new architecture
- [ ] Document single provider stack
- [ ] Update installation instructions

---

## Gap Analysis: pi-mono-rust vs Cleon Needs

### What pi-mono-rust HAS:
✅ AgentSession with full session lifecycle (start, resume, prompt, streaming)
✅ AuthStorage with OAuth for Anthropic/OpenAI/Codex
✅ ModelRegistry with provider-aware model discovery
✅ Streaming events (AgentEvent, AssistantMessageEvent)
✅ Tool execution with approval hooks (via ExtensionHost)
✅ RPC mode for JSON stdin/stdout protocol
✅ Session persistence in JSONL format
✅ Session resume via path
✅ Gemini support via provider "google"

### What's MISSING:
❌ Tool wiring in Cleon-side PyO3 bindings (basic chat mode only; tools work via CLI)

### Integration Strategy:

**Using PyO3 Native Bindings inside Cleon** (current plan)
- pi-mono-rust stays a pure Rust library
- Cleon owns the PyO3 module and wraps pi-mono-rust
- API streaming wired for Anthropic, OpenAI, Codex, and Gemini providers

---

## Verification Tests (add/expand)

### Live provider smoke tests (using existing local subscriptions)
- [x] Python smoke test for Claude via PiMonoBackend (test_pimono_backend.py) ✅ PASSED
- [x] Test Codex via PiMonoBackend ✅ PASSED
- [ ] Test Gemini via PiMonoBackend (code ready, token expired)
- [x] Verify session resume works for each provider ✅ PASSED
- [x] Verify streaming events emit consistent types across providers ✅ PASSED

### Cleon end-to-end smoke tests
- [ ] Add a Python smoke script that runs unified backend for codex/claude/gemini and prints final_message
- [ ] Validate `cleon.resume()` works across providers using the unified backend
- [ ] Validate `cleon.login()` / `cleon.auth()` now route through pi-mono-rust storage
- [ ] Jupyter magic smoke: run a minimal `%%codex`/`%%claude` cell and confirm responses flow

---

## Next Immediate Steps

1. ~~**Test PiMonoBackend with Codex** - Test with openai-codex provider~~ ✓
   - Unit tests implemented in `python/tests/test_pimono_backend.py`
   - Streaming events match expected format
   - Session persistence verified via pi-mono-rust SessionManager

2. ~~**Update magic.py** to accept use_pimono flag and pass to resolve_backend~~ ✓
   - `use_pimono` parameter added to `use()`, `register_magic()`, `resume()`
   - `resolve_backend()` calls in magic.py pass the flag
   - Backward compatible (default `use_pimono=False`)

3. ~~**Update cleon.auth()** to use pi-mono-rust OAuth functions~~ ✓
   - `login_pimono()` uses `pi_mono.anthropic_get_auth_url()`, etc.
   - Codex OAuth via `pi_mono.openai_codex_get_auth_url()`, etc.
   - Credentials stored via pi-mono-rust's AuthStorage

4. **Session resume integration** ✅ DONE
   - pi-mono-rust stores sessions in `~/.pi/agent/sessions/`
   - `cleon.resume()` with `use_pimono=True` uses PiMonoBackend's session format
   - Session persistence test: test_pimono_backend_session_persistence ✅ PASSED
   - First turn tracking test: test_pimono_backend_first_turn_tracking ✅ PASSED

5. **Validation testing** ✅ DONE
   - Live API tests: `PIMONO_LIVE_TEST=1 pytest python/tests/test_pimono_backend.py` ✅ ALL 14 TESTS PASS
   - Claude streaming: test_pimono_backend_claude_smoke ✅ PASSED
   - Codex streaming: test_pimono_backend_codex_smoke ✅ PASSED
   - Event ordering: test_streaming_events_order ✅ PASSED
   - Thread safety: test_streaming_callback_thread_safety ✅ PASSED
   - Jupyter magic smoke test with `use_pimono=True` (TODO: manual validation)
   - Verify tool streaming events flow correctly

6. **Remove legacy backends** (after validation)
   - Delete `SharedSession`, `PiProcess`, `GeminiProcess` classes
   - Remove CLI resolution functions
   - Drop `codex-*` crates from Cargo.toml

---

## Analysis Notes (2026-01-07)

### Current Code Locations
- `PiMonoBackend`: `python/src/cleon/backend.py:1176-1350`
- PyO3 bindings: `pi-mono-rust/src/python/mod.rs`
- Cleon magic: `python/src/cleon/magic.py`
- OAuth (legacy): `python/src/cleon/oauth.py`
- Auth routing: `python/src/cleon/__init__.py:369-377`

### Provider Mapping in PiMonoBackend
```python
_PROVIDER_ALIASES = {
    "codex": "openai-codex",
    "default": "openai-codex",
    "claude": "anthropic",
    "anthropic": "anthropic",
    "gemini": "google",
}
```

### pi-mono-rust API Types Supported
- `anthropic-messages` ✅
- `openai-responses` ✅
- `openai-codex-responses` ✅
- Gemini: ✅ (supported via provider "google")

REMEMBER to update this file after working for the next iteration
