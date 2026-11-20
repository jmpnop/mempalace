# 01 — chromadb 1.5.9 write bindings hold the Python GIL (local chromadb rebuild)

**Status:** applied on asgard and still required with MemPalace 3.12.0 (chromadb 1.5.9 is the newest release and
unchanged; chroma-core `main` has the same code). Not upstreamed (operator decision: local build only).
**Artifacts:** [`../patches/chromadb-1.5.9/allow_threads.patch`](../patches/chromadb-1.5.9/allow_threads.patch),
gates in [`../gates/hub-mine-lock/`](../gates/hub-mine-lock/) (`GATES-chromadb.md`, `g1.sh`–`g4.sh`, `gilwatch.py`).

## Symptom
While any `mempalace mine` ran through the hub (`mempalace serve`, launchd `com.mempalace.hub`, 127.0.0.1:8765):

- `GET /healthz` — a route that takes no lock — timed out after 10 s;
- MCP clients failed to connect (`CONNECT_TIMEOUT` after 30 000 ms) and tool calls failed with
  `palace hub proxy failed: timed out`;
- the hub log repeatedly printed `mempalace_mine has been inside the palace write path for 6x s and has not returned …
  this server now answers nothing else`.

At the time the hub process had been up 9 days (RSS ~877 MB, ~100 threads).

## Diagnosis
1. A `sample` of the hub showed the accept loop (`select.poll`) spending ~21 % of samples in `take_gil` waiting to
   re-acquire the GIL after each poll, while another thread ran native chromadb code.
2. To attribute GIL holds, `gilwatch.py` runs a mine in-process with a 5 ms ticker thread; every tick gap > 100 ms
   means some thread held the GIL that long, and the mining thread's Python stack is recorded at the gap. On a
   throwaway palace (60 files):
   - 39 of 41 gaps (11.33 of 11.6 s, worst **1.53 s**) were inside `chromadb/api/rust.py:_upsert`
     (`Collection.upsert` → `bindings.upsert`). Embedding (onnxruntime) releases the GIL and was not the cause; an
     early hypothesis (a regex pre-pass in `entity_detector`) was measured and rejected.
   - negative-control re-run on stock chromadb: 37 holds inside `_upsert`, worst 0.67 s.
3. Reading `rust/python_bindings/src/bindings.rs` at tag 1.5.9: `get` and `query` wrap their work in
   `py.allow_threads(move || self.runtime.block_on(...))`; `add`, `update`, `upsert`, `delete` and `count` call
   `self.runtime.block_on(...)` directly, i.e. while holding the GIL. On a 7 GB palace (1.17 M drawers) a single write
   batch therefore froze every Python thread of the hub, including the HTTP accept loop.

## Change
Wrap the five write/count calls exactly like `get`/`query`: add a `py: Python<'_>` parameter (PyO3 injects it; the
Python-visible signature is unchanged) and run `block_on` inside `py.allow_threads(move || …)`. 5 `allow_threads`
added, 5 `py` parameters added; 109-line diff. Nothing else changes.

## Build (reproducible)
- Source: PyPI sdist `chromadb-1.5.9.tar.gz` (sha256 `5c20e62a455c28ba…`, matches PyPI's digest); its
  `rust/python_bindings/src/bindings.rs` is byte-identical to GitHub tag 1.5.9.
- Toolchain: **Rust 1.92.0** — chromadb pins it in `rust-toolchain.toml` at the tag ("Keep this version in sync with
  rust/Dockerfile"), but the file is not in the sdist. rustc 1.99.1 fails in `chroma-blockstore` with
  `queries overflow the depth limit!` (4 errors). Raising the crate's recursion limit was rejected in favour of the
  pinned toolchain.
- `protoc` 36.2 from the official protobuf GitHub release (`protoc-36.2-osx-x86_64.zip`, sha256
  `228cc7add4616cc14ca5e80dee83209d44449a7aee95a914ae748fa374efb078`, verified) for `tonic_prost_build`.
- `RUSTUP_TOOLCHAIN=1.92.0 PROTOC=… CARGO_TARGET_DIR=… maturin build --release --strip -i <mempalace tool python>`.
- Result: `chromadb-1.5.9-cp39-abi3-macosx_10_12_x86_64.whl`. Compared with PyPI's wheel (sha256
  `60701011b5e64096…`), only `chromadb_rust_bindings.abi3.so` and wheel metadata differ; every Python file is
  identical. Bindings sha256: patched `2fedfa9949159c30…`, official `26031d103917aa58…`.

## Install / rollback
```
uv pip install --python ~/.local/share/uv/tools/mempalace/bin/python --reinstall-package chromadb --no-deps <patched wheel>
```
Rollback: the same command with PyPI's wheel. **Any `uv tool install/upgrade mempalace` silently replaces the patched
build with PyPI's** (observed during the 3.12 upgrade); re-install the patched wheel afterwards and check the bindings
sha256.

## Evidence (gates)
| Gate | Result |
|---|---|
| G1 patched build installed; add/upsert/update/delete/count/get behave | OK (negative control on stock: FAIL) |
| G2 throwaway mine, no GIL hold > 250 ms in chromadb writes | OK — 0 holds in chromadb (stock: 37, worst 0.67 s) |
| G3 hub `/healthz` ≤ 2 s throughout a mine | 170 probes, worst 1.22 s (the gate as written failed only because its 80-file mine exceeded 10 min) |
| G4 MCP stdio end-to-end add/search/delete + repair-status | OK |

After this change `/healthz` and the MCP handshake stayed responsive during mines, which removed the
`CONNECT_TIMEOUT` failures. It did **not** make tool calls answer during a mine — that is a MemPalace lock-policy
issue, see [02](02-hub-mine-lock-3.10.md).
