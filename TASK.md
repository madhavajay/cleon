# TASK

## STATUS: ✅ COMPLETED - Bundle PyO3 bindings into Cleon

PyO3 bindings have been moved from pi-mono-rust into cleon. Now `pip install cleon` provides everything in a single package with abi3 (one binary per OS).

---

## Achieved

The architecture has been restructured:
- **pi-mono-rust** is now a pure Rust library (port of pi-mono TypeScript)
- **cleon** is a single Python package that includes:
  - Python code (backend.py, magic.py, oauth.py, etc.)
  - Rust PyO3 bindings that wrap pi-mono-rust
  - Uses abi3 stable ABI (one wheel per OS, not per Python version)

## Final State

```
pi-mono-rust/              ← Pure Rust library (NO PyO3)
  └── src/lib.rs           ← Exports Rust API only
python/
  ├── rust/
  │   ├── Cargo.toml       ← Depends on pi crate, uses PyO3 + abi3
  │   └── src/lib.rs       ← PyO3 bindings (moved from pi-mono-rust)
  ├── src/cleon/           ← Python code
  │   └── _native/         ← Imports from compiled Rust
  └── pyproject.toml       ← maturin build with abi3
```

**Result:** `pip install cleon` gives you everything. One wheel per OS.

---

## Reference Information

### Rules for pi-mono-rust changes
- Keep pi-mono-rust as a pure Rust port of pi-mono TypeScript
- No PyO3 code in pi-mono-rust after this refactor
- Do not modify the TypeScript pi-mono sources; only the Rust port
- If you need to reference unported behavior, init the TS submodule for reading only:
  `git submodule update --init pi-mono`

### Execution checklist (every loop)
- Read this TASK.md and TODO.md at startup
- Do the next most important item in TODO.md
- Update TODO.md to reflect progress and next steps
- Run tests before committing: `./lint.sh`, `./test-live.sh`
- Commit after each working feature with a descriptive message

### Tests to run before commit
- `./lint.sh` - Python linting (ruff, mypy, vulture)
- `./test-live.sh` - Live API tests (requires auth)
- If touching pi-mono-rust: `cargo test` in pi-mono-rust

### Git etiquette
- Do not commit temp/debug files
- Commit after each working feature with descriptive message
- Do not co-author commits or commit .claude or other agent cruft

### Refactoring rules
- Have tests passing BEFORE refactoring
- Copy code to new location first, then delete from old
- Keep functionality identical - no "improvements" during refactor
- Run tests after each move

### DO NOT
- Add features not in the TS version to pi-mono-rust
- Skip tests to move faster
- Break existing functionality during the migration
