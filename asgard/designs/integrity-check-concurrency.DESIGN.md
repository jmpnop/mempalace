# Design: readers stay fast while a mine's post-write integrity check runs

## Decision Date
2026-11-20 (asgard). Status: **PROPOSED** — POSD Design-It-Twice; adversarial review next; no code yet.
Base: branch `asgard-local-patches` = v3.12.0 + `ef89c2cb` (hub-lock shared phase, doc 06).

## What We're Building
While the hub runs `palace._validate_palace_fts5_after_mine` (close the shared Chroma client, then `PRAGMA quick_check`
and FTS5's integrity-check over a 7.2 GB rollback-journal `chroma.sqlite3`, 8+ min on asgard), other sessions'
searches must complete in normal time — without weakening any guarantee of `backends/_inproc_sqlite.py`.

### Phase 1 — requirements (extracted from verified facts; correct me if wrong)
1. **Does:** decide whether the palace database is structurally intact after a mine, without serializing the rest of
   the process behind that decision.
2. **Callers:** `_validate_palace_fts5_after_mine` (miner, convo_miner, format_miner — inside a hub mine or a CLI
   mine), `repair` preflight, the MCP startup/reconnect integrity gate (`sqlite_integrity_status`).
3. **Receives:** a palace path. **Returns:** a list of error strings (empty = intact); `ValueError`/unopenable paths are
   reported as errors, never raised (existing contract of `_quick_check_errors`).
4. **Hard decisions:** where the scan runs relative to `palace_db_lock`; whether the shared Chroma client must be
   closed first; what a search needs during the scan (Python BM25/capacity reads = `open_reader`; Chroma vector reads
   take no lock; a Chroma client *open* writes and is exclusive).
5. **Hidden from callers:** process boundaries, lock choreography, journal mode, platform lock primitives.
6. **Edge cases:** WAL vs rollback palaces; Linux (OFD anchor) / macOS / Windows; corruption found → FTS5 heal (writes,
   exclusive); the checker cannot start; the hub process dies mid-check; CLI mine without a hub; a concurrent Chroma
   commit (must never be read torn — `test_quick_check_never_reads_a_chroma_commit_in_progress`).

**Verified facts this design rests on.** (1) `palace_db_lock(db)` is a process-local `RLock` per database file, held
by every Python sqlite3 connection from open to close (`open_reader`/`open_writer`) and by `ChromaBackend` around
writes, client open/close and collection create/delete (9 sites); Chroma reads take no lock. (2) It exists (#2302,
`beb945f6`) because two SQLite libraries share the process: fcntl locks never conflict in-process, so `quick_check`
racing a Chroma commit read torn pages (35/66 probes, rollback mode), and closing a Python connection drops the
process's POSIX locks, Chroma's included. (3) Live (gate P2): with `ef89c2cb`, searches get the hub lock at once during
the check but run 75–779 s; the search thread spent 2084/2367 samples in `rlock_acquire` (palace_db_lock). (4) The
check closes the shared Chroma client first, so a search must reopen it, and a client open writes → exclusive.
(5) **Corrected:** macOS 15.8.1 *does* support OFD locks (`F_OFD_SETLK` 90/91/92 in the SDK headers, documented in
`fcntl(2)`, exported by Python 3.13; runtime probe: an OFD read lock survives closing another descriptor, conflicts
with the same process's classic write lock and with another process's — EAGAIN). Upstream enables OFD only on Linux
and packs `struct flock` in the Linux layout, which differs on Darwin.

## Design Considered: A — "Shared Readers In-Process" (reader–writer `palace_db_lock`)
### Interface
```python
# backends/_inproc_sqlite.py
def palace_db_lock(db_path) -> PalaceLock: ...          # was: threading.RLock
class PalaceLock:
    def shared(self) -> ContextManager[None]: ...         # Python read-only connections
    def exclusive(self) -> ContextManager[None]: ...      # Python writers; Chroma writes, client open/close, DDL
    # re-entrant: exclusive holder may take shared/exclusive; shared holder asking for exclusive -> ??? (see risks)
def open_reader(db_path, *, timeout=None) -> PalaceSqliteConnection: ...   # now under shared()
def open_writer(db_path, **kw) -> PalaceSqliteConnection: ...              # under exclusive()
# palace/palace_lock.py: _validate_palace_fts5_after_mine keeps the Chroma client open during the check
```
### Data flow
The check opens a reader under `shared()`; a search's BM25/capacity reads also take `shared()` and run concurrently;
Chroma vector reads take nothing; Chroma writes/opens wait for `exclusive()` until the check closes. The client stays
open, so searches need no reopen.
### Depth Score — 5/10
Interface simplicity 1 (two modes + re-entrancy rules), implementation power 2, information hiding 1 (callers must
choose shared vs exclusive correctly at 9+5 sites), error absorption 0 (read→write upgrade deadlock must be handled by
callers), generality 1.
### Assessment
General (every Python read in the process becomes concurrent), but it rewrites the core safety primitive that five
modules and the upstream tests are built on (`test_reader_holds_the_lock_until_close` asserts exclusivity), adds a
re-entrancy/upgrade hazard (a shared holder that then needs exclusive deadlocks), and the anchor/holder registry, today
mutated under the exclusive lock, would need its own synchronization.

## Design Considered: B — "Isolated Integrity Check" (scan in a child process)
### Interface
```python
# repair.py — unchanged signatures, unchanged contract
def sqlite_integrity_errors(palace_path: str) -> list[str]: ...   # now scans in a child process
def sqlite_integrity_status(palace_path: str) -> IntegrityStatus: ...

# new: mempalace/integrity_probe.py — stdlib + sqlite3 only; importable without Chroma
def scan(sqlite_path: str) -> list[str]: ...        # quick_check + FTS5 integrity-check, today's exact logic
def main(argv) -> int: ...                           # `python -I <file> <db>` -> JSON list on stdout

# palace/palace_lock.py
#   _validate_palace_fts5_after_mine: keep the shared Chroma client open during the check (close only on Windows, as
#   upstream's mmap note requires); run the heal path as today (closes the client, open_writer, exclusive).
```
### Data flow
`sqlite_integrity_errors` starts `python -I integrity_probe.py <db>` (the same interpreter, no package import beyond
`config`), which opens its own read-only connection and runs the same scan; the parent waits on the pipe without
holding `palace_db_lock`. SQLite's normal cross-process locking now governs the scan: a concurrent Chroma commit
conflicts with the child's SHARED lock at the SQLite level (no torn read), and the child's close cannot drop the
parent's locks. Meanwhile the hub's searches take `palace_db_lock` freely for their BM25 reads, use the still-open
Chroma client for vector reads, and the hub lock admits them (`ef89c2cb`). The verdict comes back as a list.
### Depth Score — 9/10
Interface simplicity 2 (unchanged signatures), implementation power 2, information hiding 2 (process, locks, platform
hidden), error absorption 1 (a child that cannot start falls back to today's in-process scan; a crash is reported as an
error string, as unopenable paths already are), generality 2 (every caller — hub, CLI, repair, MCP gate — gets it).
### Assessment
Leaves `palace_db_lock` and every upstream lock test untouched; replaces in-process coordination with SQLite's own,
better-tested cross-process locking for the one long reader. Costs a process start per check (~0.1 s import measured
for `mempalace.config`; 1.5 s if it imported `repair` — so the probe must not).

## Phase 5 — 18-principle evaluation
| # | Principle | A | A | B | B |
|---|-----------|---|---|---|---|
| 1 | Complexity minimization | new lock modes + upgrade rules across the process | WEAK | one new leaf module, no lock changes | STRONG |
| 2 | Symptom awareness | every lock site must be re-classified (change amplification) | WEAK | callers unchanged | STRONG |
| 3 | Cause elimination | adds a dependency of 14 sites on mode choice | WEAK | removes the long reader from the in-process lock entirely | STRONG |
| 4 | Deep modules | lock interface grows | NEUTRAL | same interface, more behaviour behind it | STRONG |
| 5 | Information hiding | callers must know read vs write intent for Chroma ops | NEUTRAL | process boundary hidden in `sqlite_integrity_errors` | STRONG |
| 6 | General-purpose | all Python reads concurrent | STRONG | only long scans benefit (but all callers get it) | NEUTRAL |
| 7 | Different layer, different abstraction | lock layer becomes policy-aware | NEUTRAL | probe is a real new abstraction (isolated scan) | STRONG |
| 8 | Pull complexity downward | upgrade hazard pushed to callers | WEAK | fallback/timeout handled inside | STRONG |
| 9 | Composition | scan logic stays with repair | NEUTRAL | scan logic moved to a dependency-free module both sides import | STRONG |
| 10 | Define errors out of existence | introduces a deadlock class | WEAK | torn read impossible by construction (separate process) | STRONG |
| 11 | Design-it-twice | compared | N/A | compared | N/A |
| 12 | Comments describe non-obvious | needs per-site justification | WEAK | one module docstring explains why a process | STRONG |
| 13 | Comments-first | mode semantics hard to state | NEUTRAL | contract unchanged, easy | STRONG |
| 14 | Naming precision | shared/exclusive clear | STRONG | `integrity_probe.scan` clear | STRONG |
| 15 | Consistency | diverges from upstream tests | WEAK | mirrors upstream's own `_HOLD_SCRIPT` helper-process pattern | STRONG |
| 16 | Obviousness | reader must know who holds what | WEAK | obvious: the scan is elsewhere | STRONG |
| 17 | Strategic vs tactical | strategic but high-risk | NEUTRAL | strategic and contained | STRONG |
| 18 | No temporal decomposition | organised by what each op touches | STRONG | organised by what the scan is (isolated, read-only) | STRONG |

Counts — A: STRONG 2, NEUTRAL 6, WEAK 9 (+1 N/A). B: STRONG 16, NEUTRAL 1, WEAK 0 (+1 N/A).

## Chosen Design: B — "Isolated Integrity Check"
### Why
The problem is one exceptionally long reader holding a process-wide lock that exists only because two SQLite libraries
share a process. Moving that reader to its own process removes the reason it needed the lock, and lets SQLite's own
cross-process locking — exactly what the lock emulates — protect it. Nothing else in the process changes, so the
guarantees pinned by the upstream tests stay as they are.
### Tradeoffs Accepted
| Dimension | Recommended (B) | Rejected (A) |
|-----------|-----------------|--------------|
| Caller simplicity | unchanged | callers choose lock modes |
| Implementation complexity | one leaf module + spawn/fallback | lock rewrite + registry sync + upgrade rules |
| Extensibility | other long scans can use the probe | every Python read concurrent |
| Error handling burden | spawn failure → in-process fallback | deadlock class to prevent |
| Testability | probe testable alone; upstream tests unchanged | upstream lock tests must change |
| Naming clarity | clear | clear |
| Cost | ~0.1–0.3 s process start per check | none |
### Red Flags Addressed
- Information leakage (A: lock-mode choice leaks to 14 sites) — absent in B.
- Conjoined methods (A: acquire shared, then must not upgrade) — absent in B.
- Special-case-heavy code — B has one platform special case (Windows keeps the pre-check client close) — documented.
- Shallow modules / pass-throughs / config proliferation / generic containers / temporal decomposition: none in B.
### Remaining Risks
1. **Keeping the Chroma client open during the check.** Upstream closes it so the writer's client is closed "and WAL
   flushes before the read-only sqlite3 re-open". A *cross-process* read-only connection reads committed WAL frames
   through `-shm`, so a flush is not required for correctness; to be proven by a gate on a WAL palace (child sees every
   committed row). Windows keeps the close (mmap note).
2. **macOS cross-process lock drop (pre-existing, separate).** With the client open, the hub's own fast-path reads
   still close Python connections; on a WAL palace without the anchor that drops Chroma's locks — the bug upstream
   documents for macOS. Mitigation: the related change below.
3. **Child lifetime.** If the hub dies, the child must not linger holding SHARED: it watches its stdin and exits on EOF
   (as upstream's `_HOLD_SCRIPT` does).
4. **Interpreter/env for the child:** `sys.executable` with `-I`; the probe imports nothing from Chroma; measured import
   budget: `mempalace.config` 0.09 s.

## Related change (separate design + gates): OFD anchor on macOS
Enable `_F_OFD_SETLK` on Darwin (`fcntl.F_OFD_SETLK` exists on Python 3.13/macOS) and pack `struct flock` in the
platform's layout (Darwin `qqihh`: start, len, pid, type, whence; Linux `hhqqi`). Effect: macOS takes the anchored
path like Linux, closing the documented cross-process lock drop; upstream's `anchored_only` tests then run on macOS.

## Interface Comments
```python
def sqlite_integrity_errors(palace_path: str) -> list[str]:
    """Return SQLite and FTS5 integrity errors for the palace's chroma.sqlite3 (empty list = intact).

    Callers do not need to know that the scan runs in a separate process, how long it takes, or which locks protect it.

    palace_path: palace directory; a provably absent database returns [] (unchanged contract).
    returns: error strings exactly as today's in-process scan produces them; an unopenable path, a path the URI cannot
             carry, or a checker that died are reported as error strings, never raised.

    Design note: the scan reads the whole database and can run for minutes. In-process it had to hold
    palace_db_lock (two SQLite libraries share this process; fcntl locks do not conflict within it), which stopped
    every other reader. In its own process, SQLite's cross-process locking protects it instead and this process holds
    nothing while it runs. If the child cannot be started, the scan runs in-process as before.
    """

# mempalace/integrity_probe.py
def scan(sqlite_path: str) -> list[str]:
    """Run PRAGMA quick_check and FTS5's integrity-check on sqlite_path through a fresh read-only connection.

    Imports only the standard library and mempalace.config, so it can run in a child process without loading Chroma.
    returns: the error strings; never raises for a database that cannot be opened (reported as an error string).
    """
```
- **What each module hides:** `integrity_probe` — the scan itself and its connection rules; `repair.sqlite_integrity_errors`
  — the process boundary, fallback and lifetime; `_validate_palace_fts5_after_mine` — when the client must be closed
  (Windows, heal) and the hub shared phase.
- **Temporal decomposition watch points:** do not split "close client → spawn → wait → reopen" across callers; keep the
  heal (exclusive, in-process) separate from the read-only scan.
- **Deepest abstraction opportunity:** the same probe can serve every long read-only SQLite scan (palace audit,
  repair-status counts) — later, measured first.

## Gates (to write before code)
1. Unit: `integrity_probe.scan` == the current in-process result on an intact DB, a DB with a corrupted FTS5 index, an
   unopenable path (error string), a named pipe (refused by type).
2. Unit: during `sqlite_integrity_errors` the calling thread does **not** hold `palace_db_lock` (spy), and a concurrent
   `open_reader` in another thread completes while the scan runs.
3. Upstream: `tests/test_inproc_sqlite_locks.py` (incl. `test_quick_check_never_reads_a_chroma_commit_in_progress`,
   rollback + WAL) and the full suite pass; negative control for gate 2 on the current code.
4. WAL palace: with the client left open, the child sees every committed row (remaining risk 1).
5. Live hub: 20-file mine; during the integrity tail every search's lock wait ≤ 5 s **and** run time ≤ 2× the median
   search run time measured with no mine running.
6. Cleanup + `repair-status` OK.
