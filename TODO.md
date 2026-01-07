# TODO

## Current Status: ✅ COMPLETED - Bundle PyO3 into Cleon

The PyO3 bindings have been moved from pi-mono-rust into cleon. Now `pip install cleon` provides everything in a single package with abi3 (one binary per OS).

---

## Completed Work

### Phase 1: Create Rust wrapper in cleon ✅

- [x] Created `python/rust/Cargo.toml`
  - Depends on `pi` crate via path (`../../pi-mono-rust`)
  - PyO3 with abi3 feature (`pyo3 = { version = "0.23", features = ["abi3-py39"] }`)
  - `crate-type = ["cdylib"]`

- [x] Created `python/rust/src/lib.rs`
  - Copied content from `pi-mono-rust/src/python/mod.rs`
  - Updated imports to use `pi::` prefix (external crate)
  - All PyO3 class/function definitions preserved

- [x] Updated `python/pyproject.toml`
  - Switched build system from `hatchling` to `maturin`
  - Configured maturin for abi3
  - Module name set to `cleon._native`

---

### Phase 2: Update Python imports ✅

- [x] Updated `python/src/cleon/backend.py`
  - Changed `from pi_mono import ...` to `from cleon._native import ...`

- [x] Updated `python/src/cleon/oauth.py`
  - Changed `from pi_mono import ...` to `from cleon._native import ...`
  - Updated all references and docstrings

- [x] Updated `python/mypy.ini`
  - Changed `pi_mono` to `cleon._native` for ignore patterns

---

### Phase 3: Clean up pi-mono-rust ✅

- [x] Removed PyO3 from pi-mono-rust
  - Deleted `pi-mono-rust/src/python/mod.rs`
  - Removed `python` feature from `Cargo.toml`
  - Removed `pyo3` dependency
  - Removed `#[cfg(feature = "python")]` blocks from `lib.rs`
  - Removed `cdylib` crate-type (pure library now)

- [x] Verified pi-mono-rust is pure Rust
  - All 313 cargo tests pass
  - No PyO3 references remain

---

### Phase 4: Validation ✅

- [x] Build and test
  - `cd python && maturin develop` ✅
  - `./lint.sh` ✅
  - `./test-live.sh` ✅ (9 tests pass)

- [x] Verified single package install
  - `pip install -e python/` provides everything
  - No separate `maturin develop` in pi-mono-rust needed

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
├── rust/                        ← PyO3 wrapper
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

## abi3 Notes

Using `abi3-py39` means:
- One compiled binary works for Python 3.9, 3.10, 3.11, 3.12, 3.13+
- Wheel filename: `cleon-X.Y.Z-cp39-abi3-{platform}.whl`
- No need to build separate wheels per Python version

---

## Previous Work (Completed)

- ✅ All providers use unified PiMonoBackend
- ✅ Legacy backends removed (CodexBackend, PiBackend, GeminiBackend)
- ✅ Tool approval hooks implemented
- ✅ Session resume working
- ✅ Event streaming validated for Claude, Codex, Gemini
- ✅ lint.sh and test-live.sh scripts working
- ✅ PyO3 bindings bundled into cleon package
