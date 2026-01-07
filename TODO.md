# TODO

## Current Status: PyO3 Bindings Implemented in pi-mono-rust

### Summary

Phase 1 is largely complete. PyO3 bindings have been added to pi-mono-rust with feature-gated
Python module support. Next step is to build the Python extension and integrate it with Cleon.

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

## Phase 1.5: Build & Test Python Extension (IN PROGRESS)

### 1.5.1 Build Python extension module
- [ ] Set up maturin or pyo3-build-backend for building the extension
- [ ] Build `_pi_mono` Python module from pi-mono-rust
- [ ] Test basic import in Python: `import _pi_mono`
- [ ] Verify `PyAuthStorage` instantiation and method calls
- [ ] Verify `PyAgentSession` instantiation with a test cwd

### 1.5.2 Create Python wrapper package
- [ ] Create `python/pi_mono/__init__.py` with high-level Python API
- [ ] Add type stubs for IDE support
- [ ] Handle platform-specific library loading

---

## Phase 2: Replace Cleon Backends

### 2.1 Create unified PiMonoBackend class
- [ ] Replace `CodexBackend` class with `PiMonoBackend`
- [ ] Replace `PiBackend` class with `PiMonoBackend`
- [ ] Replace `GeminiBackend` class with `PiMonoBackend`
- [ ] Keep `AgentBackend` protocol interface unchanged

### 2.2 Update resolve_backend() function
- [ ] Map all agent names to single `PiMonoBackend` with provider config
- [ ] Pass provider/model configuration through to pi-mono-rust

### 2.3 Translate events to Cleon format
- [ ] Map `AgentEvent` types to existing event format:
  - `AgentStart` → `{agent}.agent_start`
  - `TurnStart` → `{agent}.turn_start`
  - `MessageStart/Update/End` → `{agent}.message_start/update/end`
  - `ToolExecutionStart/End` → `{agent}.tool_execution_start/end`
  - `TurnEnd` → `{agent}.turn_end`

---

## Phase 3: Session & Auth Unification

### 3.1 Unify session handling
- [ ] Remove session handling from individual backends
- [ ] Use pi-mono-rust SessionManager for all providers
- [ ] Session files stored in `~/.pi/agent/sessions/`
- [ ] Session resume uses `--resume {session_id}` pattern

### 3.2 Unify auth storage
- [ ] Remove separate oauth.py Claude OAuth handling
- [ ] Use pi-mono-rust AuthStorage for all providers
- [ ] Auth file: `~/.pi/agent/auth.json` just use what pi and pi-mono-rust use
- [ ] Update cleon.auth() to use PyO3 bindings

### 3.3 Update magic.py
- [ ] Remove provider-specific code paths
- [ ] Use unified backend for prompt assembly
- [ ] Keep template/context system working

---

## Phase 4: Cleanup & Documentation

### 4.1 Remove deprecated code
- [ ] Delete `SharedSession` class (Codex CLI wrapper)
- [ ] Delete `PiProcess` class (Pi CLI wrapper)
- [ ] Delete `GeminiProcess` class (Gemini CLI wrapper)
- [ ] Delete CLI resolution functions
- [ ] Remove legacy Codex Rust dependency: drop `codex-*` crates from `Cargo.toml` and delete the `codex` submodule usage
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
✅ PyO3 Python bindings (feature-gated)

Gemini support is not in pi-mono-rust yet but we are working on it in another process so do it last and we will update this later when its ready.

### Integration Strategy:

**Using PyO3 Native Bindings** (implemented)
- PyO3 added to pi-mono-rust with `python` feature flag
- Build with: `cargo build --features python`
- Produces `_pi_mono` Python extension module

---

## Verification Tests (add/expand)

### Live provider smoke tests (using existing local subscriptions)
- [ ] Add an opt-in Rust integration test (guarded by env var like `PI_LIVE_TESTS=1`) that runs a single short prompt against:
  - [ ] Anthropic (Claude) via pi-mono-rust
  - [ ] OpenAI Codex via pi-mono-rust
  - [ ] Gemini via pi-mono-rust (once provider support lands)
- [ ] Verify session resume works for each provider: prompt once, resume by session id/path, prompt again, assert context continuity.
- [ ] Verify streaming events emit consistent `message_start/update/end` and `turn_end` types across providers.
- [ ] Verify tool approval hooks fire and round-trip approvals for at least one tool call per provider.

### Cleon end-to-end smoke tests
- [ ] Add a Python smoke script (manual or test) that runs unified backend for codex/claude/gemini and prints final_message.
- [ ] Validate `cleon.resume()` works across providers using the unified backend.
- [ ] Validate `cleon.login()` / `cleon.auth()` now route through pi-mono-rust storage for at least Claude + Codex.
- [ ] Jupyter magic smoke: run a minimal `%%codex`/`%%claude`/`%%gemini` cell and confirm responses flow and display.

---

## Next Immediate Steps

1. **Set up maturin build** for pi-mono-rust Python extension
2. **Build and install** `_pi_mono` module locally
3. **Write Python smoke test** to verify basic functionality
4. **Create PiMonoBackend** class in Cleon using the bindings
5. **Wire up CodexBackend** replacement first, then Claude


REMEMBER to update this file after working for the next iteration
