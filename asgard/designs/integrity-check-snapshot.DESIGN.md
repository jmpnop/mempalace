# Design: integrity check on an O(1) snapshot of `chroma.sqlite3`

## Decision Date
2026-11-20

## What We're Building
After a mine writes, `palace._validate_palace_fts5_after_mine` runs `PRAGMA quick_check` over the whole
`chroma.sqlite3` (255.8 s on a 7.2 GB clone of the asgard palace; 72 % of a 20-file mine, doc 07). The check's
connection holds `palace_db_lock` for the whole scan, so every search in the hub waits behind it (doc 06: 75–779 s).
Two earlier designs failed adversarial review (`integrity-check-concurrency.DESIGN.md`, Revisions 2–3): the scan cannot
share the live file, from another process or in-process. This design makes the scan never touch the live file: take
an O(1) filesystem clone of the database inside the existing exclusive section, release everything, and check the
clone, which nothing else ever opens.

Callers: the post-mine validation (hub and CLI mines), and — through the same primitive — the MCP startup/reconnect
gate and `repair`'s preflight. Hidden from callers: whether a snapshot was used, which clone syscall, where the clone
lives, its clean-up, and the fallback.

## Measured premises (throwaway probes, the asgard palace clone and synthetic databases)
1. `clonefile(2)` of the 7,244,337,152-byte `chroma.sqlite3` on APFS: **0.5 ms**, while a read transaction was held.
   `PRAGMA quick_check` of the clone: 255.8 s, verdict `ok` (same as the live file).
2. Clone consistency against a **separate writer process** committing continuously (bounded DB ≈ 30 MB, tiny writer
   page cache so it tries to spill mid-transaction; every committed state keeps tables `a` and `b` identical):
   - rollback (`journal_mode=delete`): 300/300 clones `integrity_check` = ok, `a` = `b`, and each clone equals exactly
     the state the cloning reader saw;
   - WAL (clone `-wal` after the database, never `-shm`): 300/300 ok, `a` = `b`, never older than the reader's view;
   - **negative control** — rollback, clone *without* the read transaction: 83 of 168 clones inconsistent (freelist size
     wrong, pages referenced twice, `a` ≠ `b`), then one clone so malformed the probe could not read it. The read transaction is what makes a clone consistent.
3. In-process writers are not stopped by that read transaction: Python and Chroma's SQLite are two libraries in one
   process, and POSIX locks never conflict within a process (`backends/_inproc_sqlite.py` docstring). In-process
   consistency therefore needs `palace_db_lock` (all mempalace writers and Chroma client opens/closes) **and** no live
   Chroma client (Chroma's compactor and pool write outside `palace_db_lock` — round-2 finding A2/C1a). The post-mine
   path already closes the client before the check; that stays.

## Design Considered: A — "Snapshot inside the integrity primitive"
### Module structure
- `backends/_inproc_sqlite.py` (the one door to in-process SQLite): new `snapshot_database(db_path)` context manager —
  hides the clone syscall per platform, the read transaction across the clone, `-wal` handling, the snapshot directory,
  stale clean-up, the free-space rule; yields a path or `None` when no O(1) clone is possible.
- `repair._quick_check_errors(sqlite_path)`: checks the snapshot when one is available, the live file otherwise; a bad
  snapshot verdict is confirmed on the live file before it is returned.
### Interface
```python
# backends/_inproc_sqlite.py
@contextmanager
def snapshot_database(db_path: str) -> Iterator[Optional[str]]: ...
# repair.py — signatures unchanged
def _quick_check_errors(sqlite_path: str) -> list[str]: ...
def sqlite_integrity_errors(palace_path: str) -> list[str]: ...
def sqlite_integrity_status(palace_path: str) -> SqliteIntegrityStatus: ...
```
### Data flow
Any caller → `_quick_check_errors` → `snapshot_database` (`palace_db_lock` + read transaction for ≈ 1 ms, clone,
release) → quick_check on the clone with no mempalace lock → `[]` returned at once; non-empty → re-run on the live file
under `palace_db_lock` (today's path) and return that verdict.
### Depth score
Interface simplicity 2 · implementation power 2 · information hiding 2 · error absorption 2 · generality 2 = **10**.
### Assessment
Every caller gains without changing. But the primitive cannot know whether a Chroma client is open in this process
(premise 3): the MCP gate and repair preflight run with clients open, so their clones can be torn. Confirmation on the
live file turns a torn-bad clone into a slow correct answer, but a torn clone that happens to read `ok` would be
returned as a clean verdict for a state that never existed. A hidden precondition the primitive cannot check.

## Design Considered: B — "Snapshot verdict for a quiesced palace"
### Module structure
- `backends/_inproc_sqlite.py`: the same `snapshot_database(db_path)` primitive as A (one place for all clone
  mechanics), documented precondition: no in-process writer outside `palace_db_lock` may be live — i.e. the Chroma
  client is closed.
- `repair.py`: new `quick_check_snapshot(palace_path) -> Optional[list[str]]` — `None` when no snapshot could be taken;
  otherwise the clone's verdict. `_quick_check_errors` and the other callers are unchanged (live check, as today).
- `palace/palace_lock._validate_palace_fts5_after_mine`, the one caller that owns the quiesced state (it has just closed
  the client): close handles as today → `quick_check_snapshot` inside the exclusive section → shared phase only for
  the scan of the clone → if the clone is clean, done; if not, or if no snapshot was possible, today's path (live
  `sqlite_integrity_errors`, heal, `MineValidationError`).
### Interface
```python
# backends/_inproc_sqlite.py
@contextmanager
def snapshot_database(db_path: str) -> Iterator[Optional[str]]: ...
# repair.py
def quick_check_snapshot(palace_path: str, *, scan_phase=contextlib.nullcontext) -> Optional[list[str]]: ...
```
`scan_phase` lets the caller wrap only the scan (the mine passes `mine_shared_phase`); the clone itself always runs
inside the caller's exclusive section.
### Data flow
Mine thread: writes done → `_close_chroma_handles` (as today) → `quick_check_snapshot`: `snapshot_database` takes
`palace_db_lock` + a read transaction through `open_reader`, clones `chroma.sqlite3` (+ `-wal`), releases both (≈ 1 ms)
→ `scan_phase()` (hub: readers admitted, they reopen clients freely — the live file is not being read) → quick_check on
the clone → clone removed → verdict. Clean → return. Bad → close handles again, live `sqlite_integrity_errors` under
the exclusive section (confirms; the snapshot is never the basis of a repair), heal on the live file as today.
### Depth score
Interface simplicity 2 · implementation power 2 · information hiding 2 · error absorption 2 · generality 1 = **9**.
### Assessment
The snapshot is used only where its precondition is established and visible (the caller just closed the client);
nothing else changes behaviour. The MCP gate and repair keep today's live check (they are not the measured stall:
the gate skips palaces > 512 MB at startup; repair is an operator action). Generality is lower than A by design.

## Phase 5 — 18-principle evaluation
| # | Principle | A | A | B | B |
|---|---|---|---|---|---|
| 1 | Complexity minimization | one change point, but a hidden precondition | NEUTRAL | one change point per concern | STRONG |
| 2 | Symptom awareness | unknown unknown: "is a client open?" | WEAK | precondition owned by the caller that creates it | STRONG |
| 3 | Cause elimination | snapshot correctness depends on distant state | WEAK | dependency explicit at the single call site | STRONG |
| 4 | Deep modules | very deep | STRONG | deep primitive, thin caller | STRONG |
| 5 | Information hiding | hides too much: hides a precondition it cannot check | NEUTRAL | hides mechanics, exposes the precondition | STRONG |
| 6 | General-purpose | all callers | STRONG | primitive general; one caller today | NEUTRAL |
| 7 | Different layer, different abstraction | yes | STRONG | yes (`snapshot_database` ≠ `quick_check_snapshot`) | STRONG |
| 8 | Pull complexity down | yes | STRONG | yes, except the precondition | STRONG |
| 9 | Composition | check + snapshot fused | NEUTRAL | snapshot primitive reusable by audit/counts later | STRONG |
| 10 | Define errors out of existence | torn clone with "ok" not defined away | WEAK | torn clone impossible where used; fallback = today | STRONG |
| 11 | Design it twice | — | N/A | — | N/A |
| 12 | Comments describe the non-obvious | must explain when the snapshot lies | WEAK | the precondition is the one comment | STRONG |
| 13 | Comments-first | hard to write an honest contract | WEAK | contract writable in three sentences | STRONG |
| 14 | Naming precision | `_quick_check_errors` silently means "maybe a snapshot" | WEAK | `quick_check_snapshot` says what it is | STRONG |
| 15 | Consistency | one door kept | STRONG | one door kept; mirrors the existing close-then-check order | STRONG |
| 16 | Obviousness | the gate's verdict source is invisible | WEAK | visible at the call site | STRONG |
| 17 | Strategic | broad but fragile | NEUTRAL | durable | STRONG |
| 18 | No temporal decomposition | organised by what it hides | STRONG | organised by what it hides (clone mechanics vs. verdict) | STRONG |
A: STRONG 7, NEUTRAL 4, WEAK 6. B: STRONG 16, NEUTRAL 1, WEAK 0.

## Chosen Design: B — "Snapshot verdict for a quiesced palace"
### Why
A clone is a consistent image only when every writer that could touch the file is excluded for the instant of the
clone: other processes by SQLite's read transaction (proven, with a negative control), mempalace's own writers by
`palace_db_lock`, and Chroma's internal writers only by having no live client. Only the post-mine path establishes the
last condition, and it is also the path that produces the measured stall. B puts the snapshot exactly there and leaves
every other caller on today's proven path.
### Tradeoffs Accepted
| Dimension | B (chosen) | A (rejected) |
|---|---|---|
| Caller simplicity | one caller changes | no caller changes |
| Implementation complexity | primitive + one helper + one call site | primitive + verdict routing inside the primitive |
| Extensibility | primitive reusable (audit, counts) by callers that can quiesce | every check silently changes |
| Error handling burden | fallback = today's code path | confirmation logic inside the primitive |
| Testability | primitive testable alone; caller's order testable | needs open-client scenarios for every caller |
| Naming clarity | explicit | overloaded |
### Red Flags Addressed
- Hidden precondition (A) → stated and established at the one call site.
- Special-case-heavy code → one fallback rule: "no snapshot → today's path".
- Conjoined methods → `snapshot_database` is a context manager; the clone cannot outlive its block.
- Information leakage → clone paths, syscalls and the snapshot directory never leave `_inproc_sqlite`.
### Remaining Risks (for review round)
1. **Copy-on-write space.** Pages written to the live file during the scan are duplicated; bound ≤ the database size.
   Rule: take a snapshot only when free space on the volume ≥ the database size (+ `-wal`); else fall back.
2. **Unsupported filesystems.** APFS `clonefile` (HFS+ → `ENOTSUP`); Linux `ioctl(FICLONE)` (Btrfs, XFS with reflink,
   bcachefs; ext4 → `EOPNOTSUPP`); Windows has no portable call (ReFS block cloning needs `FSCTL_DUPLICATE_EXTENTS`) →
   fall back. Never a byte copy: `copy_file_range` may silently copy 7 GB, so it is not used.
3. **Stale clones** after a crash or kill: snapshots live in `<palace>/.integrity-snapshots/`, named with the owner pid;
   the primitive removes entries whose pid is not alive before cloning, and always removes its own in `finally`.
   Other tools that walk the palace directory must ignore the dot-directory — to verify (`repair`, `export`, sync).
4. **Hot journal / WAL at clone time.** Rollback: the read transaction guarantees no hot journal and no uncommitted
   page in the file (premise 2). WAL: `-wal` is cloned after the database; a torn last frame is discarded by WAL
   recovery on the clone (premise 2); `-shm` is never cloned (the clone rebuilds it).
5. **Verdict equivalence.** A clean clone certifies the state at clone time, which is the state the mine produced
   (writers excluded); later writes are not covered — the same as today's check, which also ends before later writes.
6. **SQLite build used on the clone.** The same Python `sqlite3` as today's check, opened on a private path; the clone
   gets its own `palace_db_lock` key and no anchor/holder of the live file is involved.
7. **Tests that pin the close-then-check order** stay valid: the order is unchanged; the snapshot happens between them.

## Interface Comments
```python
@contextmanager
def snapshot_database(db_path: str) -> Iterator[Optional[str]]:
    """Yield the path of an O(1) filesystem clone of the SQLite database at ``db_path``, or ``None``.

    The clone is a consistent image of one committed state: during the clone this holds ``palace_db_lock(db_path)``
    and a read transaction on the live file, which excludes mempalace's own writers and every writer in other
    processes. It cannot exclude writers in this process that bypass ``palace_db_lock`` — a live Chroma client's
    compactor and connection pool — so callers must have closed Chroma's clients for this palace first.

    Callers do not need to know which clone call is used, where the clone lives, or how stale clones are removed.

    Yields ``None`` (and the caller should fall back to checking the live file) when the filesystem cannot clone in
    O(1), when free space is below the database size, or when any step of taking the clone fails. Never copies bytes.
    The clone and its sidecars are removed when the block exits, whatever happens inside it.

    Design note: the live file's locks are held only for the clone itself (about a millisecond for 7 GB on APFS);
    whatever the caller does with the clone holds no lock on the live database.
    """

def quick_check_snapshot(palace_path, *, scan_phase=contextlib.nullcontext) -> Optional[list[str]]:
    """PRAGMA quick_check of a snapshot of the palace's ``chroma.sqlite3``; ``None`` when no snapshot could be taken.

    Precondition: this process holds no live Chroma client for ``palace_path`` (see ``snapshot_database``).

    ``[]`` means the snapshot — the state committed when it was taken — is clean. A non-empty list is the snapshot's
    report; callers confirm it on the live file before acting, because a repair must never be based on a copy.

    @param scan_phase  context manager wrapped around the scan only (not the clone); the hub's mine passes
                       ``mine_shared_phase`` so other requests run during the scan.
    """
```

## Information Hiding Map
| Module | Hidden Design Decision |
|---|---|
| `_inproc_sqlite.snapshot_database` | clone syscall per platform, lock + read transaction across the clone, `-wal` rule, snapshot directory, stale clean-up, free-space rule |
| `repair.quick_check_snapshot` | that the scan runs on a copy, and how its connection is opened |
| `palace_lock._validate_palace_fts5_after_mine` | when a snapshot is trustworthy (after its own close) and that a bad verdict is confirmed live |

## Temporal Decomposition Watch Points
- Do not split `snapshot_database` into "lock / clone / unlock" helpers for callers to sequence.
- The confirm-on-live step belongs to the caller that acts on the verdict, not to the snapshot primitive.

## Gates (written before code)
1. **Primitive, rollback + WAL torture** (as the probe in `asgard/evidence/`, made a test): external writer process, ≥ 300
   snapshots each mode → 0 inconsistent; negative control (no read transaction) must produce torn clones.
2. **In-process exclusion:** a mempalace writer thread (`ChromaCollection` write under `palace_db_lock`) running during
   snapshots → 0 inconsistent clones.
3. **Fallbacks:** non-cloning filesystem (HFS+ disk image on macOS, ext4 loop image on Linux CI if available), low free
   space (simulated), clone failure (injected) → `None`, and the mine path then runs today's live check.
4. **Clean-up:** clone removed on success, on exception and on `KeyboardInterrupt`; a stale clone of a dead pid is
   removed by the next snapshot; a live pid's clone is left alone.
5. **Verdict routing:** clean snapshot → no live check; corrupt snapshot (fixture: a database with a corrupted FTS5
   page) → live check runs, heal runs on the live file, `MineValidationError` as today when the heal declines.
6. **Upstream suite** green apart from the two failures documented in doc 06; the close-before-check order tests pass
   unchanged.
7. **Live hub (gate P2 repeated):** 20-file mine through the hub with concurrent searches; during the integrity tail
   every search's lock wait ≤ 5 s and run time ≤ 2× the idle median; the mine's tail no longer holds
   `palace_db_lock` (hub sample shows no `rlock_acquire` stall); `repair-status` OK; no files left in
   `.integrity-snapshots/`.
