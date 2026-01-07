# TODO

## Current Status: 🔄 Restructure - Bundle PyO3 into Cleon

### Goal
Make cleon a single `pip install` package by moving PyO3 bindings from pi-mono-rust into cleon, using abi3 for cross-Python-version compatibility.

---

## Phase 1: Create Rust wrapper in cleon

- [ ] Create `python/rust/Cargo.toml`
  - Depend on `pi` crate via path (`../pi-mono-rust`)
  - Add PyO3 with abi3 feature (`pyo3 = { version = "0.23", features = ["abi3-py39"] }`)
  - Set `crate-type = ["cdylib"]`

- [ ] Create `python/rust/src/lib.rs`
  - Copy content from `pi-mono-rust/src/python/mod.rs`
  - Update imports to use `pi::` prefix (external crate)
  - Keep all PyO3 class/function definitions

- [ ] Update `python/pyproject.toml`
  - Switch build system from `hatchling` to `maturin`
  - Configure maturin for abi3
  - Set module name to `cleon._native`

---

## Phase 2: Update Python imports

- [ ] Update `python/src/cleon/backend.py`
  - Change `from pi_mono import ...` to `from cleon._native import ...`

- [ ] Update `python/src/cleon/oauth.py`
  - Change `import pi_mono` to `from cleon import _native`
  - Update all `pi_mono.` references

- [ ] Update tests
  - Ensure tests import from new location
  - Verify `./test-live.sh` still works

---

## Phase 3: Clean up pi-mono-rust

- [ ] Remove PyO3 from pi-mono-rust
  - Delete `pi-mono-rust/src/python/mod.rs`
  - Remove `python` feature from `Cargo.toml`
  - Remove `pyo3` dependency
  - Remove `#[cfg(feature = "python")]` blocks from `lib.rs`

- [ ] Verify pi-mono-rust is pure Rust
  - Run `cargo test` to ensure library still works
  - Verify no PyO3 references remain

---

## Phase 4: Validation

- [ ] Build and test
  - `cd python && maturin develop`
  - `./lint.sh`
  - `./test-live.sh`

- [ ] Verify single package install
  - `pip install -e python/` should provide everything
  - No separate `maturin develop` in pi-mono-rust needed

- [ ] Test abi3 wheel
  - Build wheel: `maturin build --release`
  - Verify single `.whl` file works on multiple Python versions

---

## Files to Modify

| File | Action |
|------|--------|
| `python/rust/Cargo.toml` | CREATE - PyO3 wrapper crate |
| `python/rust/src/lib.rs` | CREATE - Move from pi-mono-rust |
| `python/pyproject.toml` | MODIFY - Switch to maturin |
| `python/src/cleon/backend.py` | MODIFY - Update imports |
| `python/src/cleon/oauth.py` | MODIFY - Update imports |
| `pi-mono-rust/src/python/mod.rs` | DELETE |
| `pi-mono-rust/Cargo.toml` | MODIFY - Remove python feature |
| `pi-mono-rust/src/lib.rs` | MODIFY - Remove python module |

---

## abi3 Notes

Using `abi3-py39` means:
- One compiled binary works for Python 3.9, 3.10, 3.11, 3.12, 3.13+
- Wheel filename: `cleon-X.Y.Z-cp39-abi3-{platform}.whl`
- No need to build separate wheels per Python version

PyO3 abi3 config in Cargo.toml:
```toml
[dependencies]
pyo3 = { version = "0.23", features = ["abi3-py39", "extension-module"] }
```

---

## Architecture After Completion

```
pi-mono-rust/                    ← Pure Rust (no PyO3)
├── Cargo.toml
├── src/
│   ├── lib.rs                   ← Public Rust API
│   ├── agent/
│   ├── coding_agent/
│   └── ...

python/
├── rust/                        ← PyO3 wrapper (NEW)
│   ├── Cargo.toml               ← Depends on pi crate + PyO3/abi3
│   └── src/lib.rs               ← Python bindings
├── src/cleon/
│   ├── __init__.py
│   ├── _native/                 ← Compiled Rust module lands here
│   ├── backend.py               ← Uses cleon._native
│   ├── magic.py
│   └── oauth.py
└── pyproject.toml               ← maturin build
```

---

## Previous Work (Completed)

- ✅ All providers use unified PiMonoBackend
- ✅ Legacy backends removed (CodexBackend, PiBackend, GeminiBackend)
- ✅ Tool approval hooks implemented
- ✅ Session resume working
- ✅ Event streaming validated for Claude, Codex, Gemini
- ✅ lint.sh and test-live.sh scripts working
