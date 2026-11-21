# Design: integrity check on an O(1) snapshot of `chroma.sqlite3`

**State:** Revision 4 (2026-11-21), after five adversarial review rounds; round 5 returned CONDITIONAL PASS from all
three reviewers (0 critical; A1 and C1 high, each with a contained fix). This document states only the current design.
Revisions 1–3, including the Design-It-Twice comparison (Design A "snapshot inside the integrity primitive", depth 10,
rejected because the primitive cannot know whether a Chroma client is open; Design B "snapshot verdict for a quiesced
palace", chosen), are in git history (`03078a7f`, `da19c843`, `4a6d252f`); the review history is summarised at the end.

**Scope decision (operator, 2026-11-21):** both journal modes are supported — rollback (`journal_mode=delete`, what
Chroma 1.5 creates and what the asgard palace uses) and WAL.

## What we're building
After a mine writes, `palace._validate_palace_fts5_after_mine` runs `PRAGMA quick_check` over the whole
`chroma.sqlite3` (255.8 s on a 7.2 GB clone of the asgard palace; 72 % of a 20-file mine, doc 07). Its connection holds
`palace_db_lock` for the whole scan, so every search in the hub waits behind it (doc 06: 75–779 s). Two earlier designs
failed review (`integrity-check-concurrency.DESIGN.md`): the scan cannot share the live file. This design makes the
scan never touch the live file: inside the mine's exclusive section, take an O(1) filesystem clone of the database
while a separate process holds a read transaction on it, release everything, and check the clone, which nothing else
opens.

Callers: the post-mine validation (hub and CLI mines). Hidden from callers: whether a snapshot was used, the clone
syscall, where the clone lives, its clean-up, the journal mode, and the fallback reasons.

## Measured premises
1. `clonefile(2)` of the 7,244,337,152-byte `chroma.sqlite3` on APFS: 0.5 ms, under a read transaction; stays O(1)
   after 250k copy-on-write extents (0.2–0.6 ms). `quick_check` of the clone: 255.8 s, `ok`.
2. Clone consistency under a read transaction held by a **separate process**: rollback 300/300 and WAL 300/300
   consistent against a Python writer process; 0/300 torn with this process's own Chroma client writing in a loop
   (rollback), 0/1,000+ against a Chroma writer process, 0 in every round-5 torture run. **Negative control:** without
   the read transaction 83 of 168 rollback clones were inconsistent, and 7/300 with an in-process Chroma writer.
3. Rollback mode: the helper's SHARED lock blocks every COMMIT of every process, this one included (POSIX locks of
   another process conflict). WAL mode: there is no exclusion; consistency comes from the read mark (slot 0: the
   checkpointer cannot backfill past it; slot > 0: the WAL cannot restart) plus cloning the database before `-wal`, and
   checksum/salt recovery on the clone.
4. A `mode=ro` connection writes the live `-shm` when it is the **first opener** (measured, rounds 5 A and C: a WAL
   database with `-wal`/`-shm` and no lock holder had `-shm` rewritten; after a clean close a `mode=ro` open with the
   bundled SQLite 3.50.4 created `-wal` and `-shm` and left them; Apple's 3.43.2 refused). As a second reader it writes
   only its read mark. `_inproc_sqlite` already keeps a WAL palace guarded in this process (macOS: a holder process
   whose idle WAL connection holds SHARED and the `-shm` DMS lock; Linux: an anchor connection with OFD guards), and
   `ChromaBackend.close_palace` releases only the Chroma client, never that guard.
5. An orphaned helper keeps a lock it inherited; a blocking `flock` behind it waited > 8 s (round 5 C, `orphan.py`) and
   4.68 s (round 5 A, `e1_flock.py`). One leaked clone that diverged from the live file pushed free space under the
   threshold on a 2.6 GB image (round 5 C, `lowspace.py`).

## Mechanism
`snapshot_database(db_path) -> ContextManager[Snapshot]` in `backends/_inproc_sqlite.py` (the module that owns
in-process SQLite access, its locks and guards). Every exception in steps 1–5 becomes `SETUP_FAILED` with its text as
`detail`; nothing escapes `__enter__`, so a failed speed-up can never fail a mine.

**Entry (inside the caller's exclusive section; bounded, see G12):**
0. `_SNAPSHOTS` false → `DISABLED`. No database file → `NO_DATABASE`. Filesystem without O(1) clones (allow-list:
   macOS APFS by `f_fstypename`; Linux btrfs `0x9123683E`, xfs `0x58465342` by `f_type`) → `UNSUPPORTED_FS`.
1. Create `<palace>/.integrity-snapshots/` (mode 0755) and open its `.lock` (mode 0644, the palace's lock-file rule:
   another user who can write the palace must be able to open it). `flock(LOCK_EX | LOCK_NB)`, retried for at most
   0.5 s → `SNAPSHOT_BUSY`. Holding it, remove every other entry (clones leaked by a killed snapshot). Relation to
   `mine_palace_lock`: the post-mine caller already holds that per-palace lock, so two mines never snapshot the same
   palace at once; the `flock` exists for what that lock cannot see — a helper orphaned by its dead parent.
2. Free space (`f_bavail`) ≥ 1.1 × (db size + `-wal` size), measured **after** the sweep → else `LOW_SPACE`.
3. Journal mode from the header (bytes 18–19: 1 rollback, 2 WAL). WAL: `-wal` present without `-shm` → `WAL_UNGUARDED`
   (a crash leftover; the helper would be a first opener). Otherwise, under `palace_db_lock`, establish this module's
   guard exactly as `open_reader` does (`_ensure_holder` on macOS, `_ensure_anchor` + OFD guards on Linux; bounded by
   `_HOLDER_READY_SECONDS`, 2 s); guard not holding → `WAL_UNGUARDED`. With the guard holding, a live connection keeps
   SHARED and the DMS lock, so the helper is never a first opener.
4. Under `palace_db_lock(db_path)`, with a 2.0 s deadline from acquiring it: spawn the helper
   (`python -I -c <self-contained script> db_path clone_prefix`, imports nothing from mempalace,
   `pass_fds=(lock_fd,)`). The helper arms `signal.alarm(3)` first (it can never outlive 3 s, and its copy of the
   `flock` dies with it); records `stat(db_path)` identity; opens the live database `mode=ro` — never read-write, never
   `_HOLD_SCRIPT`'s sidecar rule; `BEGIN` + one read (SHARED, or a read mark in WAL); clones the database, then `-wal`
   in WAL mode, to unique names; checks the identity again; ends the transaction; prints one line: `done`, `busy`
   (any `SQLITE_BUSY`/`database is locked`), or `failed <detail>` (including an identity change), and exits.
   `done` → release `palace_db_lock`. `busy` or the deadline → **release `palace_db_lock` first**, then kill and reap
   the helper and remove its names → `DATABASE_BUSY`. `failed` → `SETUP_FAILED`.
5. Yield `Snapshot(skip=None)`. It owns the `flock` and the clone names until `quick_check` or exit.

**`Snapshot.quick_check()` (the caller runs it in `mine_shared_phase`, so nothing here blocks searches):**
a. WAL clone: fold it with a read-write connection on the private clone (`wal_checkpoint(TRUNCATE)`,
   `journal_mode=delete`); require header bytes 18–19 = 1 and no clone sidecar names; else return `None` with
   `skip = FOLD_FAILED`. The fold's time grows with `-wal` size; it runs in the shared phase, not the exclusive one.
b. Open the clone `mode=ro&immutable=1` with plain `sqlite3` (never `open_reader`), unlink every clone name, release
   the `flock`.
c. `PRAGMA quick_check`. The open in (b) and the scan are wrapped in `except (sqlite3.Error, ValueError)` — the same set
   as `repair._quick_check_errors` — and both go through one helper, `integrity_errors(rows=None, exc=None)`, defined
   in `_inproc_sqlite` and imported by `repair` (a downward import). Returns `list[str]`, `[]` meaning clean.

**Exit (every path after the helper was spawned, in `finally`):** kill and reap the helper if it is still alive; close
the clone connection; unlink any remaining clone name; release the `flock`. The kernel frees the clone's blocks when
the connection closes or the process dies, and releases the `flock` on any death.

**Logging:** `snapshot_database` logs its own outcome; each `SnapshotSkip` member carries its level — `DISABLED` debug,
`UNSUPPORTED_FS` info once per process, every other member warning with `detail`. Callers log nothing about skips.

## Interface
```python
# backends/_inproc_sqlite.py
class SnapshotSkip(enum.Enum):
    """Why no snapshot was used. Named by cause, not by mechanism; each carries its log level."""
    DISABLED = ("debug", "snapshots are turned off")
    NO_DATABASE = ("warning", "no database file")
    UNSUPPORTED_FS = ("info", "the filesystem cannot clone a file in O(1)")
    SNAPSHOT_BUSY = ("warning", "another snapshot of this palace is in progress")
    LOW_SPACE = ("warning", "not enough free space to hold a diverging copy")
    WAL_UNGUARDED = ("warning", "WAL database without a guard this process can establish")
    DATABASE_BUSY = ("warning", "a writer held the database past the deadline")
    SETUP_FAILED = ("warning", "the copy could not be made")
    FOLD_FAILED = ("warning", "the copied WAL could not be folded into the copy")

class SnapshotUsageError(RuntimeError): ...

class Snapshot:
    skip: Optional[SnapshotSkip]   # None: a copy was taken and quick_check() may run
    detail: str                    # cause text for a skip, "" otherwise
    def quick_check(self) -> Optional[list[str]]: ...

@contextmanager
def snapshot_database(db_path: str) -> Iterator[Snapshot]: ...

def integrity_errors(rows=None, exc: Optional[BaseException] = None) -> list[str]: ...
```

## Interface comments
```python
@contextmanager
def snapshot_database(db_path: str) -> Iterator[Snapshot]:
    """A private, consistent copy of the SQLite database at ``db_path`` for one long read.

    The copy is one committed state of the live file: a short-lived helper process holds a read transaction on the
    live file while it clones it, under ``palace_db_lock`` for at most 2 s. In rollback mode that read lock blocks
    every writer of every process, this one included; in WAL mode the read mark keeps the copied database and WAL
    mutually consistent. The database file and its ``-wal`` are never written; in WAL mode the helper takes a read
    mark in ``-shm`` as any reader does, and only after this module's guard for the file is holding, so it is never
    the connection that initialises the wal-index.

    Callers do not need to know how the copy is made, where it lives, how it is cleaned up after a crash, which
    platforms and journal modes support it, or how a skip is logged. Closing this process's Chroma clients first is
    only so their caches need not be rebuilt mid-check; a client left open does not make the copy wrong.

    Yields a ``Snapshot``. ``snapshot.skip`` is None when a copy was taken; otherwise it names the cause, already
    logged, and the caller checks the live file as before. No exception from making the copy leaves this function.
    Nothing in the block holds a lock on the live database.

    Design note: the copy costs no space until the live file changes; its blocks are freed when the block exits or
    the process dies.
    """

def quick_check(self) -> Optional[list[str]]:
    """``PRAGMA quick_check`` of the copy: ``[]`` when clean, else the findings, exactly as the live check reports
    them (an error opening or scanning the copy is a finding). ``None`` when no verdict could be reached
    (``skip`` then says why). May be called once, inside the block, on the thread that entered it; otherwise
    ``SnapshotUsageError`` — also when ``skip`` was set at entry.
    """
```

## Data flow — `_validate_palace_fts5_after_mine`
writes done → `_close_chroma_handles` (as today) → `with snapshot_database(db) as snap:` →
`if snap.skip is None: with mine_shared_phase(): errors = snap.quick_check()` → block exits (copy freed) →
- `errors == []` → return.
- no snapshot, or `errors is None` → today's code path exactly.
- `errors` non-empty → warning with the findings → today's code path; if the live check is then clean, warning
  "snapshot verdict disagreed with the live file" with both verdicts.
`palace_lock` looks `snapshot_database` up on the `_inproc_sqlite` module at call time: that is the test seam (G8).
The fallback runs after the block, never inside it (inside would pin the copy through a second 256 s scan).

Known difference on the taken-with-errors path (rare: corruption): today the live check starts right after
`_close_chroma_handles`; here it starts after a shared phase in which hub readers may have reopened clients. Heal
safety holds — the second shared phase's re-close still runs before the FTS5 heal — but the disagreement warning is
emitted from that state. Cost on that path: one extra scan of the copy (≈ 256 s here) before today's path.

## Information hiding map
| Module | Hidden decision |
|---|---|
| `_inproc_sqlite.snapshot_database` | clone syscall per platform, read transaction in a helper process, WAL guard and fold, snapshot directory, orphan and stale-copy clean-up, free-space rule, skip logging |
| `_inproc_sqlite.integrity_errors` | how integrity rows and exceptions become findings, shared by the copy and the live check |
| `palace._validate_palace_fts5_after_mine` | when a verdict from a copy is enough and when the live file must be checked |

## Watch points
- The fallback stays outside the `with`; the verdict routing stays in the caller, the copy mechanics in the module.
- The helper never opens the live file read-write and never uses `_HOLD_SCRIPT`'s sidecar rule.
- Any new step after the helper is spawned must be inside the `finally` that kills and reaps it.
- The sweep must run before the free-space measurement.

## Gates (written before code)
- G1 Torture, Python writer process, rollback and WAL, ≥ 300 each → 0 inconsistent copies; negative control (no read
  transaction) shows torn copies.
- G2 Torture, Chroma `PersistentClient` writer process, ≥ 1,000 → 0 inconsistent; negative control ≥ 1 torn.
- G3 Torture, Chroma client in this process writing, rollback **and WAL**, ≥ 300 each → 0 inconsistent.
- G4 WAL storms (RESTART/TRUNCATE checkpoint loop, `wal_autocheckpoint=10`, a reader on a fully backfilled WAL) → 0.
- G5 Kills and leaks: SIGKILL of the parent during the helper, during the fold, during the scan; SIGKILL of the helper
  mid-clone; an exception injected between spawn and the helper's answer; `launchctl kickstart -k` of a test hub
  mid-scan → no helper alive 3.5 s after its spawn, no later snapshot waits more than 0.5 s for the `flock`, after the
  next snapshot no stale entry remains, and free space returns.
- G6 Live files: the database and `-wal` have the same inode, size, mtime and content hash before and after across
  G1–G5; `-shm` is never created, truncated or replaced (same inode and size); fixtures: a hot rollback journal
  (`SETUP_FAILED`, journal left in place), `-wal` without `-shm` (`WAL_UNGUARDED`, nothing created), a quiesced WAL
  palace in a CLI-style process (the module guard is established as for any read, the snapshot is taken, and the
  sidecars that exist afterwards are the guard's).
- G7 Every `SnapshotSkip` member produced by a fixture and logged once at its level, and the mine then follows today's
  path: flag off; no database; HFS+ image; a second holder of `.lock`; low space; low space caused by a leaked copy
  (swept first → not `LOW_SPACE`); WAL without `-shm`; a writer holding the database past 2 s (classified
  `DATABASE_BUSY`, never `SETUP_FAILED`); an unwritable `.integrity-snapshots/`; a helper crash; a database replaced
  during the clone; a `-wal` copy removed before the fold.
- G8 Routing through the seam, on every platform: clean → no live check; findings → today's path; `None` → today's
  path; disagreement warning; `SnapshotUsageError` after the block, from another thread, on a second call, and on a
  skipped snapshot. The same routing with a real copy on APFS (and btrfs/xfs where available).
- G9 No copy key in `_anchors`, `_holders` or `_locks` after a snapshot; the live file's macOS holder (same pid) and
  Linux anchor are unchanged by a snapshot.
- G10 `copy_palace_dir` and the carry-over of palace files skip `.integrity-snapshots/`.
- G11 ENOSPC on a 200 MB APFS image: below the threshold → `LOW_SPACE`; a leaked copy is swept before measuring; at the
  threshold with full rewrites during the scan → no `SQLITE_FULL` on the live file.
- G12 Bounds under a busy in-process Chroma writer, 300 snapshots per mode: `palace_db_lock` held ≤ 2.05 s (rollback)
  and ≤ 4.05 s (WAL, including the guard); the whole entry of `snapshot_database` ≤ 5 s p100, including a sweep of a
  leaked 7 GB copy; no helper alive more than 3.5 s after its spawn.
- G13 Upstream suite green apart from doc 06's two known failures (autouse fixture pins `_SNAPSHOTS=False`); new
  tests on macOS APFS; Linux runs where available (ext4 → `UNSUPPORTED_FS`; btrfs/xfs loop image → snapshot), else
  recorded as not run.
- G14 Live hub: a 20-file mine with concurrent searches; the hub log shows the snapshot was taken; during the check
  every search's lock wait ≤ 5 s and run ≤ 2× its idle median; no `rlock_acquire` stall in a hub sample;
  `repair-status` OK; `.integrity-snapshots/` holds only `.lock`.

## Review history
- **Rounds 1–2** (on the concurrency designs and this design's revision 1): the scan cannot share the live file;
  in-process consistency needs no live Chroma client and `palace_db_lock`; a clone must be taken under a read
  transaction (negative control: 83/168 torn).
- **Round 3 → revision 2:** the read transaction moved into a separate helper process so it excludes this process's
  own writers; consistency claims measured in both journal modes.
- **Round 4 → revision 3:** read-only helper, opaque `Snapshot` handle, `SnapshotSkip` enum, `.lock` with `pass_fds`,
  WAL fold with header check, free space including `-wal`, test flag, pre-spawned helper, a descriptor tripwire.
- **Round 5 → revision 4** (A, B, C: conditional pass). Folded in:
  - a `mode=ro` helper writes the live `-shm` as a first opener, and the cited sidecar rule opens read-write (A1, B3,
    C3) → the helper never uses that rule; WAL requires this module's guard to be holding (else `WAL_UNGUARDED`);
    `-wal` without `-shm` is refused; the claim is narrowed to the database file and `-wal`;
  - a leaked helper keeps the `flock` forever and a blocking `flock` wedges later mines (C1, A4) → `LOCK_NB` with a
    0.5 s retry (`SNAPSHOT_BUSY`), kill and reap in `finally` on every path, a 3 s self-destruct alarm in the helper;
  - the free-space check ran before the sweep (C2) → sweep first;
  - a quiesced WAL palace was always refused (C4) → the guard is established as for any read, then the snapshot runs;
  - contention reported as a failure (C5) → `SQLITE_BUSY` is `DATABASE_BUSY`;
  - the 2 s bound counted kill and reap (C6, B10) → `palace_db_lock` is released before kill and reap; G12 restated;
  - the fold had no bound inside the exclusive section (C7, A4) → the fold moved into `quick_check`, in the shared
    phase; G12 bounds the whole entry;
  - the descriptor tripwire cannot be built and warns on every Linux mine (A3, B6) → deleted;
  - pre-spawning bought ~100 ms for a protocol and an identity race (A5) → the helper is spawned under the lock with no
    handshake and checks the file's identity before and after the clone;
  - the `.lock` duplicated `mine_palace_lock` without saying so, and mode 0700 contradicted the palace rule (A6) →
    relation stated, modes 0755/0644;
  - setup failures in steps 1 and 5 had no skip member (B1) → `SETUP_FAILED`, nothing escapes `__enter__`;
  - the copy's exception set differed from the live check's (B2) → one `integrity_errors` helper, the same
    `(sqlite3.Error, ValueError)` around open and scan;
  - `taken` duplicated `skip`, and skip names leaked the mechanism and their log policy into the caller (B4, B5) →
    `skip` only, members named by cause and carrying their level, logged by the module; `DISABLED` at debug;
  - an unanchored "shared helper" statement (B7) and an inexact "today's path exactly" (B8) → stated precisely above;
  - pinning the flag off left the default path untested off APFS (B9) → a call-time seam and routing tests on every
    platform;
  - WAL support kept although Chroma 1.5 creates rollback palaces (A2): the operator chose to support both modes.
