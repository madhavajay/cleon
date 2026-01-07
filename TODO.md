# TODO

## Current Status: ✅ MIGRATION COMPLETE - All Providers Use PiMonoBackend

### Summary (2026-01-07)

**MAJOR MILESTONE: pi-mono-rust migration complete!**

All providers (Codex, Claude, Gemini) now use `PiMonoBackend` via pi-mono-rust.
Live testing validated for all three providers. Legacy code removed.

All legacy backends have been removed. Cleon now exclusively uses `PiMonoBackend` via pi-mono-rust for all providers (Codex, Claude, Gemini). This eliminates:
- The `codex` submodule and `codex-*` crate dependencies
- The legacy `src/main.rs` CLI wrapper
- The `python/cleon` Rust PyO3 wrapper (`_cleon` module)
- Legacy backend classes: `SharedSession`, `CodexBackend`, `PiBackend`, `PiProcess`, `GeminiBackend`, `GeminiProcess`
- The `use_pimono` flag (now everything uses pi-mono-rust)

**What remains:**
- `PiMonoBackend` class in `backend.py` - the single unified backend
- `pi-mono-rust` submodule with PyO3 bindings (`pi_mono` Python module)
- Clean Python package using `hatchling` instead of `maturin`

### Changes Made

1. **Root Cargo.toml** - Converted to workspace-only config excluding pi-mono-rust
2. **src/main.rs** - DELETED (no longer needed)
3. **python/cleon/** - DELETED (Rust PyO3 wrapper no longer needed)
4. **pyproject.toml** - Switched from maturin to hatchling, removed `_cleon` references
5. **backend.py** - Removed all legacy classes, kept only `PiMonoBackend` and `resolve_backend()`
6. **magic.py** - Removed `use_pimono` parameter from all functions
7. **__init__.py** - Removed `_cleon` imports, `SharedSession` export, simplified `auth()`/`login()`
8. **tests/test_pimono_backend.py** - Updated to test new API without legacy references

### Test Status

All unit tests pass:
```
tests/test_pimono_backend.py - 10 passed, 9 skipped (live tests)
tests/test_templates.py - 16 passed
tests/test_gemini_magic.py - 1 passed
tests/test_import_cleon.py - 1 passed
```

Live tests (require `PIMONO_LIVE_TEST=1`):
- Claude (anthropic) - VALIDATED ✓
- Codex (openai-codex) - VALIDATED ✓
- Gemini (google-gemini-cli) - VALIDATED ✓
- Session resume - VALIDATED ✓
- Event streaming - VALIDATED ✓

### Build Instructions

```bash
# Build pi_mono Python module
cd pi-mono-rust
source .venv/bin/activate  # Python 3.13 venv
maturin develop --features python

# Install cleon package (now uses hatchling)
pip install -e python/

# Run unit tests
python -m pytest python/tests/test_pimono_backend.py -v

# Run live tests (requires valid auth)
PIMONO_LIVE_TEST=1 python -m pytest python/tests/test_pimono_backend.py -v
```

---

## Completed Phases

### Phase 1: PyO3 Bindings (COMPLETE)
- [x] PyO3 module in `pi-mono-rust/src/python/mod.rs`
- [x] Core API exported: AgentSession, AuthStorage, auth functions
- [x] Event streaming working for all providers

### Phase 2: PiMonoBackend (COMPLETE)
- [x] Created unified `PiMonoBackend` class
- [x] Implements `AgentBackend` protocol
- [x] Event translation to Cleon format
- [x] Live tested with Claude and Codex

### Phase 3: Session & Auth Unification (COMPLETE)
- [x] pi-mono-rust handles all session storage (`~/.pi/agent/sessions/`)
- [x] pi-mono-rust handles all auth storage (`~/.pi/agent/auth.json`)
- [x] `cleon.auth()` and `cleon.login()` use pi-mono-rust OAuth

### Phase 4: Legacy Cleanup (COMPLETE)
- [x] Removed `codex-*` crate dependencies from Cargo.toml
- [x] Deleted `src/main.rs` (Codex CLI wrapper)
- [x] Deleted `python/cleon/` (Rust PyO3 _cleon wrapper)
- [x] Removed `SharedSession`, `CodexBackend`, `PiBackend`, `GeminiBackend` classes
- [x] Removed `use_pimono` flag from all APIs
- [x] Switched to hatchling build system

---

## Remaining Work

### Documentation Updates (COMPLETE)
- [x] Update README with new architecture
- [x] Document single provider stack
- [x] Update installation instructions

### Gemini Support (COMPLETE)
- [x] Fixed provider alias: "gemini" -> "google-gemini-cli" (matches model registry)
- [x] pi-mono-rust has full google-gemini-cli streaming support
- [x] Fixed auth check to detect `~/.gemini/oauth_creds.json` credentials
- [x] Fixed JSON parsing for float expiry_date field in Gemini CLI creds
- [x] Validate PiMonoBackend with Gemini live test - WORKING ✓

### End-to-End Validation (COMPLETE)
- [x] Jupyter magic smoke test: `%%codex` and `%%claude` cells
- [x] Verify `cleon.resume()` works in Jupyter context
- [x] Verify tool streaming events flow correctly
- [x] Removed obsolete `test_magic.py` (was testing removed SharedSession)

---

## Future Work (Not Blocking)

### Tool Approval Hooks
- [ ] Wire `on_approval` callback in `PiMonoBackend.send()`
- [ ] Currently ignored with `del on_approval  # TODO: implement approval hooks`
- [ ] pi-mono-rust PyO3 bindings need tool support (basic chat mode only currently)
- [ ] Approval flow: magic.py calls `_prompt_approval()` but callback is not wired

**Implementation Requirements (analyzed 2026-01-07):**

To implement approval hooks, changes are needed in three places:

#### Sub-task 1: pi-mono-rust Agent core (`pi-mono-rust/src/agent/mod.rs`)
- [ ] Add `ApprovalRequest` variant to `AgentEvent` enum:
  ```rust
  ApprovalRequest {
      tool_call_id: String,
      tool_name: String,
      args: Value,
      command: Option<String>,  // shell command if applicable
      cwd: Option<String>,       // working directory
      reason: Option<String>,    // why approval needed
  }
  ```
- [ ] Add `ApprovalResponse` enum: `Approve | ApproveSession | Deny | Abort`
- [ ] Add `on_approval: Option<Box<dyn Fn(&ApprovalRequest) -> ApprovalResponse>>` to `AgentOptions`
- [ ] Modify `execute_tool_calls()` in `agent/mod.rs:528` to:
  1. Before executing tool, call `on_approval` callback if set
  2. Based on response: proceed, skip tool, or abort entire loop
  3. Track session-approved tools to auto-approve subsequent calls
- [ ] Add tests for approval flow in `pi-mono-rust/src/agent/tests/`

#### Sub-task 2: PyO3 bindings (`pi-mono-rust/src/python/mod.rs`)
- [ ] Add `PyApprovalRequest` class with fields matching Rust struct
- [ ] Add `set_approval_callback(callback: PyObject)` method to `PyAgentSession`
- [ ] In Python callback, convert PyApprovalRequest to dict for magic.py
- [ ] Convert Python string response ("approve", "deny", "abort") to Rust enum
- [ ] Handle GIL properly - callback runs during agent loop, must acquire GIL

#### Sub-task 3: PiMonoBackend (`python/src/cleon/backend.py`)
- [ ] Store `on_approval` callback in `PiMonoBackend.__init__`
- [ ] In `send()`, if `on_approval` provided, call `_session.set_approval_callback()`
- [ ] Create wrapper that translates pi-mono-rust approval dict to cleon format
- [ ] Expected event format for magic.py `_prompt_approval()`:
  ```python
  {
      "kind": "approval",  # or tool name
      "command": "...",     # shell command
      "cwd": "...",         # working directory
      "reason": "...",      # why approval needed
  }
  ```

**Why this is complex:**
- pi-mono-rust's `execute_tool_calls()` runs synchronously inside the agent loop
- Approval requires pausing execution and waiting for user input
- Current design: callback-based (sync) - callback blocks until user responds
- Alternative: channel-based (async) - would require bigger refactor

**Recommended approach:**
Use synchronous callback that blocks the agent loop until approval received.
This works because:
1. Jupyter notebook cells already block during `%%codex` execution
2. `_prompt_approval()` in magic.py uses `time.sleep(0.05)` polling anyway
3. No need for async - the agent loop can simply wait

This is not blocking the migration since:
1. Basic chat and tool streaming work (events flow to Jupyter)
2. pi-mono-rust handles tool execution internally
3. Approval hooks are only needed for interactive approval prompts in notebooks
4. This can be added later when pi-mono-rust PyO3 exposes tool hooks

---

## pi-mono-rust Public API Surface (for Cleon)

**Analysis completed 2026-01-07** - Cleon needs these pi-mono-rust APIs:

### Currently Exposed & Working ✅

| API | Location | Cleon Usage |
|-----|----------|-------------|
| `AgentSession::new()` | `python/mod.rs:203` | `PiMonoBackend.__init__` |
| `AgentSession::prompt(text)` | `python/mod.rs:324` | `PiMonoBackend.send()` |
| `AgentSession::subscribe(callback)` | `python/mod.rs:336` | Event streaming |
| `AgentSession::session_id()` | `python/mod.rs:391` | Session tracking |
| `AgentSession::session_file()` | `python/mod.rs:396` | Session persistence |
| `AgentSession::switch_session(path)` | `python/mod.rs:376` | Resume sessions |
| `AgentSession::new_session()` | `python/mod.rs:369` | Fresh session |
| `AgentSession::get_last_assistant_text()` | `python/mod.rs:405` | Extract response |
| `AgentSession::dispose()` | `python/mod.rs:445` | Cleanup |
| `AgentSession::abort()` | `python/mod.rs:362` | Cancel operation |
| `get_agent_dir()` | `python/mod.rs:765` | Path resolution |
| `anthropic_get_auth_url()` | `python/mod.rs:676` | OAuth flow |
| `anthropic_exchange_code()` | `python/mod.rs:683` | OAuth flow |
| `anthropic_refresh_token()` | `python/mod.rs:708` | Token refresh |
| `openai_codex_get_auth_url()` | `python/mod.rs:726` | OAuth flow |
| `openai_codex_exchange_code()` | `python/mod.rs:733` | OAuth flow |
| `openai_codex_refresh_token()` | `python/mod.rs:749` | Token refresh |

### Gaps vs Cleon Usage ⚠️

| Missing API | Cleon Need | Status |
|-------------|------------|--------|
| Tool approval callback | `on_approval` in `backend.send()` | Not implemented |
| `AgentSession::set_approval_callback()` | Block for user approval | Not implemented |

### Event Types Streamed

All these events flow from pi-mono-rust → Python → Jupyter:

```
AgentSessionEvent::Agent(AgentEvent::*)
  - AgentStart, AgentEnd
  - TurnStart, TurnEnd
  - MessageStart, MessageUpdate, MessageEnd
  - ToolExecutionStart, ToolExecutionUpdate, ToolExecutionEnd
AgentSessionEvent::AutoCompactionStart
AgentSessionEvent::AutoCompactionEnd
```

**Missing event type:**
- `ApprovalRequest` - needed for tool approval flow

---

## Architecture Summary

```
Cleon (Python)
├── backend.py
│   └── PiMonoBackend (unified backend)
│       └── pi_mono.AgentSession (PyO3)
├── magic.py (Jupyter integration)
├── oauth.py (login_pimono -> pi_mono OAuth)
└── __init__.py (public API)

pi-mono-rust (Rust submodule)
├── AgentSession (session management)
├── AuthStorage (credential storage)
├── StreamEventEmitter (event streaming)
└── PyO3 bindings (src/python/mod.rs)
```

### Provider Mapping
```python
_PROVIDER_ALIASES = {
    "codex": "openai-codex",
    "default": "openai-codex",
    "claude": "anthropic",
    "anthropic": "anthropic",
    "gemini": "google-gemini-cli",
    "google": "google-gemini-cli",
    "google-gemini-cli": "google-gemini-cli",
}
```

---

REMEMBER to update this file after working for the next iteration
