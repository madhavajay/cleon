# TASK

Goal
Rework Cleon to use only the pi-mono-rust library for all providers (Codex, Claude, Gemini) instead of spawning separate CLI processes. The new flow should import pi-mono-rust as a Rust library, provide native subscription support as pi-mono-rust does, and feed the Jupyter prompt pipeline through a unified provider/session interface. Resume, login/auth, and session handling must be consistent across providers.

Current state (read first, with pointers)
- Provider backends live in `python/src/cleon/backend.py`:
  - CodexBackend uses the cleon Rust CLI and PyO3 bindings (`cleon` binary + `_cleon`).
  - PiBackend spawns the Node pi CLI in RPC mode for Claude.
  - GeminiBackend spawns the gemini CLI and parses JSON events.
- Prompt assembly and session plumbing live in `python/src/cleon/magic.py` (context, templates, approvals, async queue, resume/stop).
- Login/auth lives in `python/src/cleon/oauth.py` and `python/src/cleon/__init__.py` (Claude OAuth, Codex auth bindings).
- Per-agent config is in `python/src/cleon/settings.py`.
- Codex-only Rust CLI is in `src/main.rs` and wired in `Cargo.toml`.
- Rust port reference and parity status are in `pi-mono-rust/docs/rust-port-plan.md`.

Target state (definition of done)
- Only pi-mono-rust is used for provider execution (no `pi` CLI and no `gemini` CLI in Cleon).
- Remove any usage of the legacy Codex Rust repo/submodule and its crates; Cleon should not depend on `codex-*` crates once pi-mono-rust is integrated.
- Codex, Claude, and Gemini all go through a single pi-mono-rust interface with the same session, resume, and auth behaviors.
- Jupyter prompt pipeline (context, templates, approvals, event streaming) still works, but calls a unified backend implemented via pi-mono-rust bindings.
- Login/auth and resume are consistent across providers, backed by pi-mono-rust auth/session storage.
- Documentation updated to reflect the single provider stack.
- Provider order: finish Codex + Claude (pi subscription) first; do Gemini after those are stable.

Rules for pi-mono-rust changes
- If you need to change pi-mono-rust, create a branch inside the submodule and make changes there.
- Do not modify the TypeScript pi-mono sources; only the Rust port.
- If you need to reference unported behavior, init the TS submodule for reading only:
  `git submodule update --init pi-mono`

Next most important step (do this now)
1) Inspect pi-mono-rust public APIs and identify what Cleon needs: session start/resume, prompt send, streaming events, tool approval hooks, and auth/login entry points. Write down the minimal Rust API surface Cleon should call and the gaps vs Cleon usage in `python/src/cleon/backend.py` and `python/src/cleon/magic.py`. Update TODO.md with concrete sub-tasks based on the gaps you find.

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
