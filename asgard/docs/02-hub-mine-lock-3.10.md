# 02 — A mine held the hub's exclusive lock for its whole run (MemPalace 3.10.0 local patch, superseded by 3.12.0)

**Status:** applied on asgard to MemPalace 3.10.0, verified, then **removed** by the upgrade to 3.12.0, which solves the
same problem upstream with a different design (yielding the write lock; see [04](04-upgrade-to-3.12.0.md)). Kept here
for the record and for the Rust-port design.
**Artifacts:** [`../patches/mempalace-3.10.0/01-hub-mine-lock.patch`](../patches/mempalace-3.10.0/01-hub-mine-lock.patch)
(330 lines, against 3.10.0), gates [`../gates/hub-mine-lock/`](../gates/hub-mine-lock/) (`GATES-mempalace.md`,
`g5_unit.py`, `g6_live.py`).

## Symptom (after the chromadb fix in 01)
`/healthz` stayed fast during a mine, but every palace tool call from other sessions queued behind it:
`mempalace_list_drawers` waited **187 s**; the hub log still said `mempalace_mine … answers nothing else`.

## Cause (3.10.0)
`service.classify_tool("mempalace_mine")` returns `"maintenance"`; `mcp_server/http._http_dispatch` gives every tool
that is not a read the exclusive side of `_HTTP_REQUEST_LOCK` for the whole call. The miner already takes its own
per-palace lock (`mine_palace_lock`), so whole-call exclusivity added no safety between mines and only blocked readers.

## Change (3.10.0)
- New `mempalace/hub_lock.py`: a *long operation* holds the hub `_RWLock` **shared** for its run and steps up to
  **exclusive** only inside `exclusive()` (released shared hold → write → re-take shared). Process-wide counter
  `long_operation_active()`.
- `http._http_dispatch`: `mempalace_mine` runs inside `hub_lock.long_operation()`.
- `backends/chroma.py: ChromaCollection._write_lock`: `hub_lock.exclusive()` around every chromadb write.
- `palace/palace_lock._validate_palace_fts5_after_mine`: exclusive only to close the shared client and for the FTS5
  auto-heal; the `PRAGMA quick_check` runs with the shared hold.

`mine_palace_lock` is re-entrant per *process*, so another hub thread's write passes it; mutual exclusion of chromadb
writes therefore comes from the RW lock, and there is no lock-order cycle (verified by reading `palace_lock.py`).

## Bugs found while making it work (each found by a live gate, fixed, re-verified)
1. **Client torn down under the mine** — four G6 runs died with `'RustBindingsAPI' object has no attribute
   'bindings'`. A temporary `traceback` in chromadb's `RustBindingsAPI.stop()` named the caller each time:
   - `ChromaBackend._client()` and `_session._get_client()` treat any `chroma.sqlite3` mtime change as an external
     writer and drain/reset chromadb's shared System; with reads now running alongside the mine, an outside
     `add_drawer` reset the client the mine was writing through. Fix: while a long operation runs, an mtime-only change
     re-baselines instead (an inode change or missing DB still rebuilds).
   - the session cache's **cold open** compares against baselines of 0, so `inode_changed` read as true on the very
     first open and still reset the System. Fix: an inode change counts only when a previous inode was known.
   - the #1315 search retry and `mempalace_reconnect` also reset the shared client: skipped / refused while a mine
     runs on another thread.
2. **Post-mine `quick_check` blocked everyone** — with the client race fixed, a search still waited > 400 s: the hub
   was inside `PRAGMA quick_check` over the 7.2 GB `chroma.sqlite3` (sampled: 100 % of samples in `sqlite3BtreeNext`,
   an extra read-only fd open) for ≥ 8 min, under the exclusive lock. Fix: quick_check under the shared hold.

## Evidence (gates, final run on 3.10.0)
| Gate | Result |
|---|---|
| G5 unit: `hub_lock` vs the hub's real `_RWLock` (6 scenarios, deadlock timeouts) | OK; negative control (`exclusive` disabled) FAILS scenario b |
| G6 live: 20-file mine through the hub with concurrent calls | mine rc 0, 733 s, 216 drawers; 44 searches worst **15.9 s** (was 187 s / > 400 s); outside `add_drawer` 5.0 s; `/healthz` worst 0.8 s over 353 probes |
| G7 cleanup + `repair-status` | probe wing empty, drawers/closets OK |
| G4 MCP end-to-end | OK |

## Superseded by 3.12.0
3.12.0 puts `mempalace_mine` in `_HTTP_YIELDING_TOOLS`: it still takes the exclusive lock (plus `_HTTP_MINE_LOCK`) but
`palace.mine_yield_hook(_HTTP_REQUEST_LOCK.yield_write)` periodically hands it to waiting calls. The 3.10 patch was not
carried forward; whether the post-mine `quick_check` still blocks on 3.12 is tracked in [05](05-open-issues.md).
