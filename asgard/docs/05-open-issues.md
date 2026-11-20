# 05 — Open issues on 3.12.0 (asgard) and the Rust-port plan

## 1. The end of a mine still blocks every other call for minutes
Measured on 3.12.0 (gate U3): after the write phase a search waited **578 s** for the hub lock. The mine's tail —
`palace._validate_palace_fts5_after_mine` (closes the shared chroma client, then `PRAGMA quick_check` over a 7.2 GB
`chroma.sqlite3`, then the FTS5 auto-heal if needed) and, once, the hallways import — runs under the exclusive lock
without yielding. On 3.10.0 the same `quick_check` was sampled at ≥ 8 min (100 % of samples in `sqlite3BtreeNext`).
Fix direction proven on 3.10.0 (doc 02): exclusive only while the shared client is closed and for the auto-heal; the
quick_check reads through its own read-only sqlite3 connection and can run with readers admitted.
**Update:** ported to 3.12.0 as commit `ef89c2cb` (doc 06): searches now get the hub lock at once during the check
(578 s → 0 ms), but then block on `palace_db_lock`, which the check's own connection holds for the whole scan
(search run times 75–779 s). Still open: a reader–writer form of `palace_db_lock` (doc 06, "Next").

## 2. Searches got slower: 13–27 s per search on 3.12.0 (≈ 6 s on 3.10.0)
3.12's `CALL` lines show the time is in `run`, not lock `wait`. On 3.10.0 a search was 3.4 s `hnsw_capacity_status`
(recomputed) + 2.6 s query. Not yet profiled on 3.12.0.

## 3. Local chromadb build must survive upgrades
Every `uv tool install/upgrade mempalace` reinstalls PyPI's chromadb. After any upgrade: re-install the patched wheel
and check the bindings sha256 (`2fedfa99…`). Candidate upstream contribution to chroma-core (not done; operator
decision so far: local only).

## 4. Rust port (planned, not started)
Operator decision: port the performance-bottleneck component in this fork. Measured hot paths, in order: client
open / HNSW metadata checks (`make_client` 9.45 s, mostly `quarantine_invalid_hnsw_metadata`), the search-time
`hnsw_capacity_status` probe (3.4 s), and the post-mine `quick_check` scan. The boundary chosen in the single-cache
design ([`../patches/mempalace-3.10.0/02-session-single-cache.DESIGN.md`](../patches/mempalace-3.10.0/02-session-single-cache.DESIGN.md)),
`ChromaBackend.get_collection / close_palace / reset_palace`, is the seam: one client per palace, everything slow
behind it. Next step: re-measure on 3.12.0 (issues 1–2), then design the port (POSD Design-It-Twice) before code.
