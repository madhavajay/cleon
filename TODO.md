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

To implement approval hooks, changes are needed in two places:

1. **pi-mono-rust Agent core** (`pi-mono-rust/src/agent/mod.rs`):
   - Add an `on_approval` callback to `AgentOptions`
   - Modify `execute_tool_calls()` to emit approval request before execution
   - Wait for approval response (approve/deny/abort) before proceeding
   - This requires async/sync flow changes since current tool execution is synchronous

2. **PyO3 bindings** (`pi-mono-rust/src/python/mod.rs`):
   - Expose approval callback mechanism to Python
   - `PyAgentSession::prompt()` would need to handle async approval requests

3. **PiMonoBackend** (`python/src/cleon/backend.py`):
   - Wire `on_approval` Python callback through PyO3 to Rust

**Why this is complex:**
- pi-mono-rust's `execute_tool_calls()` runs synchronously inside the agent loop
- Approval requires pausing execution and waiting for user input
- This would need either async/await support or a channel-based approach

This is not blocking the migration since:
1. Basic chat and tool streaming work (events flow to Jupyter)
2. pi-mono-rust handles tool execution internally
3. Approval hooks are only needed for interactive approval prompts in notebooks
4. This can be added later when pi-mono-rust PyO3 exposes tool hooks

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
