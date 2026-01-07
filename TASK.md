# TASK

## STATUS: ✅ MIGRATION COMPLETE (2026-01-07)

All providers (Codex, Claude, Gemini) now use PiMonoBackend via pi-mono-rust.
See TODO.md for details on what was completed and remaining future work.

---

## Original Goal (ACHIEVED)
Rework Cleon to use only the pi-mono-rust library for all providers (Codex, Claude, Gemini) instead of spawning separate CLI processes. The new flow should import pi-mono-rust as a Rust library, provide native subscription support as pi-mono-rust does, and feed the Jupyter prompt pipeline through a unified provider/session interface. Resume, login/auth, and session handling must be consistent across providers.

## Current State (as of 2026-01-07)
- All providers use unified `PiMonoBackend` in `python/src/cleon/backend.py`
- Legacy backends removed: `CodexBackend`, `PiBackend`, `GeminiBackend`, `SharedSession`
- Legacy Rust code removed: `src/main.rs`, `python/cleon/` PyO3 wrapper
- Login/auth uses pi-mono-rust OAuth (`oauth.py` → `login_pimono()`)
- Session resume works via `AgentSession.switch_session()`
- Event streaming validated for all three providers

## Target State (ACHIEVED)
- ✅ Only pi-mono-rust is used for provider execution (no `pi` CLI and no `gemini` CLI in Cleon).
- ✅ Removed legacy Codex Rust repo/submodule and its crates.
- ✅ Codex, Claude, and Gemini all go through a single pi-mono-rust interface.
- ✅ Jupyter prompt pipeline works with unified backend.
- ✅ Login/auth and resume are consistent across providers.
- ✅ Documentation updated to reflect the single provider stack.

## Remaining Future Work (Not Blocking)
- Tool approval hooks: Wire `on_approval` callback in `PiMonoBackend.send()`
- See TODO.md for details

---

## Reference Information

Rules for pi-mono-rust changes
- If you need to change pi-mono-rust, create a branch inside the submodule and make changes there.
- Do not modify the TypeScript pi-mono sources; only the Rust port.
- If you need to reference unported behavior, init the TS submodule for reading only:
  `git submodule update --init pi-mono`

Execution checklist (every loop)
- Read this TASK.md and TODO.md at startup.
- Do the next most important item in TODO.md.
- Update TODO.md to reflect progress and next steps.
- Run tests relevant to what you changed (see below) before committing.
- Commit after each working feature with a descriptive message.
- Update `pi-mono-rust/docs/rust-port-plan.md` "Current Rust Status" section when pi-mono-rust changes.

Tests to run before commit
- Always: `./clippy.sh`
- If touching pi-mono-rust: run its Rust tests (`cargo test` in `pi-mono-rust` or `pi-mono-rust/rs-test.sh`).
- If touching Cleon Python: run relevant Python tests or smoke-check Jupyter integration.

Git etiquette
- Do not commit temp/debug files.
- Run `./clippy.sh` before committing.
- Update `pi-mono-rust/docs/rust-port-plan.md` "Current Rust Status" section.
- Commit after each working feature with descriptive message.
- Do not co-author commits or commit .claude or other agent cruft.

Refactoring rules
- Have tests passing BEFORE refactoring.
- Copy code to new location first, then delete from old.
- Keep functionality identical - no "improvements" during refactor.
- Run tests after each move.

DO NOT
- Add features not in the TS version.
- Skip tests to move faster.
- Let main.rs grow past 500 lines.
- Implement streaming without tests.

As you start to understand more about the task you can update the bottom of this file
