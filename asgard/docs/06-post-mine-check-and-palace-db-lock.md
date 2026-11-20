# 06 — The post-mine integrity check blocks readers twice: the hub lock, then `palace_db_lock`

**Status:** step 1 (hub lock) implemented in commit `ef89c2cb`, deployed on asgard, unit-tested; the live gate shows it
is **necessary but not sufficient** — readers now pass the hub lock and block on `palace_db_lock` instead. Step 2 is
open (see "Next").
**Gates:** [`../gates/post-mine-shared/`](../gates/post-mine-shared/) (`GATES.md`, `d1.sh`, `p2.sh`, `p2_live.py`, `p3.sh`).

## Problem (from 04, gate U3 on 3.12.0)
After a mine's writes, `palace._validate_palace_fts5_after_mine` closes the shared Chroma client and runs
`PRAGMA quick_check` over the 7.2 GB `chroma.sqlite3` (on SQLite ≥ 3.44 the pragma also walks the FTS5 index — the `sqlite3Fts5IndexIntegrityCheck` frames sampled below; the explicit FTS5 `'integrity-check'` INSERT runs only in repair's post-rebuild gate). On 3.12.0 it runs under the
mine's exclusive hub request lock with no yield point, so a search waited **578 s** for the lock.

## Step 1 — let readers through the hub lock during the check (`ef89c2cb`)
- `mcp_server/http._RWLock.write_downgraded()`: the thread holding the write lock holds it shared for a body; readers
  enter even ahead of a queued writer; no other writer runs until the body ends; the writer then re-takes it.
- `palace.mine_share_hook()` / `palace.mine_shared_phase()` (in `palace/mine_lock.py`): the same host-hook pattern as
  `mine_yield_hook()` / `mine_yield_point()`; the hub installs it for yielding tools; without a hub it is a no-op.
- `_validate_palace_fts5_after_mine` runs `sqlite_integrity_errors` inside `mine_shared_phase()` and, if the check
  failed after a shared phase, closes the client again before the FTS5 heal.
- Tests: `TestRWLockWriteDowngraded` (4) in `tests/test_mcp_http_transport.py`, `tests/test_mine_shared_phase.py` (4).
  Related suites: 140 passed, 6 skipped (baseline 132/6). Negative control on v3.12.0: 6 of the 8 new tests fail (the
  2 that pass cover deliberately unchanged behaviour). Full suite: 8534 passed, 2 failed — `test_cli_import_under_startup_budget`
  also fails on unmodified v3.12.0 on this Intel Mac; `test_armed_watchdog_waits_for_interpreter_shutdown` passes
  alone on both trees and failed only under `-n auto` load. ruff clean.
- Deployed: `uv tool install <fork> --reinstall` + patched chromadb wheel; the installed package is byte-identical to
  the fork commit (gate D1). The version stays `3.12.0` because the repo's version guard requires all ten version
  sources to agree; the deployed build is identified by commit instead.

## Live result (gate P2: 20-file mine through the hub) — FAIL as written
From the hub's own `CALL <tool> <mode> wait=… run=…` lines:

| Phase | Search lock wait | Search run time |
|---|---|---|
| mine start (first search) | 81.2 s | 26.0 s |
| write phase (3 searches) | 0.7–2.7 s | 14.9–19.0 s |
| integrity-check tail (3 searches) | **0 ms, 0 ms, 8.7 s** (578 s before step 1) | **75 s, 384 s, 779 s** |

The mine: `SLOW CALL mempalace_mine write wait=0ms run=911669ms ok`. Outside `add_drawer` 3.8 s run. Gate P3 (cleanup,
`repair-status`): OK.

## Why the admitted readers still crawl: `palace_db_lock`
`backends/_inproc_sqlite.py` documents it: ChromaDB opens `chroma.sqlite3` through the SQLite statically linked into
`chromadb_rust_bindings`, while MemPalace's fast paths (status, taxonomy, `list_drawers`, BM25, the integrity gate,
repair) use Python's `sqlite3` — two SQLite libraries in one process. POSIX locks never conflict within a process, so
`palace_db_lock(db_path)` — a process-local re-entrant lock per database file — is held by **every** Python connection
from open to close, and by `ChromaBackend` around its writes and client opens. The integrity check's connection holds it
for the whole scan; a search admitted in step 1 then waits on it (for the Python BM25 read and for re-opening the Chroma
client that the check closed). A `sample` of the hub during the tail showed the search thread with 2084 of 2367 samples
in `rlock_acquire`, one thread in `sqlite3Fts5IndexIntegrityCheck`, and the palace in `journal_mode=delete`.

The lock exists for data safety (a `quick_check` overlapping a Chroma commit reads a torn page set; closing a Python
connection drops the process's fcntl locks, Chroma's included), so it must not simply be bypassed.

## Next (open)
- A reader–writer form of `palace_db_lock` (Python read-only connections shared; Chroma writes, Chroma client opens and
  Python writers exclusive) would let searches read during the check while keeping Chroma commits exclusive. It changes
  a core safety mechanism and needs a design review and its own tests (`tests/test_inproc_sqlite_locks.py`) before code.
- The 81 s wait at the start of the mine (before the first `mine_yield_point`) is a separate, smaller issue.
- Searches take 13–27 s even with no mine running (05, issue 2) — not explained by this lock alone; to profile.
