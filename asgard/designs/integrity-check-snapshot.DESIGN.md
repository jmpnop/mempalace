# Design: integrity check on an O(1) snapshot of `chroma.sqlite3`

**State:** Revision 5 (2026-11-21), after six adversarial review rounds; round 6 returned CONDITIONAL PASS from all
three reviewers (0 critical; three high findings with separate roots, each folded in below). This document states only
the current design. Revisions 1–4 are in git history (`03078a7f`, `da19c843`, `4a6d252f`, `393faf9d`), including the
Design-It-Twice comparison (Design A "snapshot inside the integrity primitive", rejected because the primitive cannot
know whether a Chroma client is open; Design B "snapshot verdict for a quiesced palace", chosen). The review history is
summarised at the end.

**Scope decision (operator, 2026-11-21):** both journal modes are supported — rollback (`journal_mode=delete`, what
Chroma 1.5 creates and what the asgard palace uses) and WAL.

## What we're building
After a mine writes, `palace._validate_palace_fts5_after_mine` runs `PRAGMA quick_check` over the whole
`chroma.sqlite3` (255.8 s on a 7.2 GB clone of the asgard palace; 72 % of a 20-file mine, doc 07). Its connection holds
`palace_db_lock` for the whole scan, so every search in the hub waits behind it (doc 06: 75–779 s; observed again on
2026-11-21 during doc 08's gates: 114 s and 322 s searches behind a hub mine). Two earlier designs failed review
(`integrity-check-concurrency.DESIGN.md`): the scan cannot share the live file. This design makes the scan never
touch the live file: inside the mine's exclusive section, take an O(1) filesystem clone of the database while a
separate process holds a read transaction on it, release everything, and check the clone, which nothing else opens.

Callers: the post-mine validation (hub and CLI mines). Hidden from callers: the clone syscall, where the clone lives,
its clean-up, the journal mode, and how each skip is logged. Visible to the caller, on purpose: whether a verdict came
from a copy (`quick_check()` returns `None` when it did not), because only the caller knows what to do without one.

## Measured premises
1. `clonefile(2)` of the 7,244,337,152-byte `chroma.sqlite3` on APFS: 0.5 ms, under a read transaction; stays O(1)
   after 250k copy-on-write extents (0.2–0.6 ms). `quick_check` of the clone: 255.8 s, `ok`.
2. Clone consistency under a read transaction held by a **separate process**: rollback 300/300 and WAL 300/300
   consistent against a Python writer process; 0/300 torn with this process's own Chroma client writing in a loop
   (rollback), 0/1,000+ against a Chroma writer process, 0 in every round-5 torture run. **Negative control:** without
   the read transaction 83 of 168 rollback clones were inconsistent, and 7/300 with an in-process Chroma writer.
3. Rollback mode: the helper's SHARED lock blocks every COMMIT of every process, this one included; a Chroma 1.5.9
   writer waits on it and succeeds afterwards (round 6 B: adds held 0.5–12 s all succeeded, latency = hold). WAL mode:
   there is no exclusion; consistency comes from the read mark (slot 0: the checkpointer cannot backfill past it;
   slot &gt; 0: the WAL cannot restart) plus cloning the database before `-wal`, and checksum/salt recovery on the clone.
4. A `mode=ro` connection writes the live `-shm` when it is the **first opener**; as a second reader it writes only its
   read mark. This module's guard (macOS: a holder process; Linux: an anchor connection with OFD guards) keeps a WAL
   palace from ever having the helper as first opener. Establishing the guard on a quiesced WAL palace creates `-wal`
   (0 bytes) and `-shm` (32 KiB) and leaves them to the guard (round 6 B E3, C e9).
5. **Any descriptor opened outside SQLite and closed again drops every POSIX lock the process holds on that inode**
   (`backends/_magic.py:10-21`). Round 6 measured both sides: in the parent, one `open`/`read(20)`/`close` let a
   second process take RESERVED beside this process's writer, and let another process unlink a live `-wal` under an
   open connection (A E1); in a helper inside `BEGIN` + read, one `os.open`/`os.close` let an external commit
   through while the helper still reported `in_transaction` (C e10). A `mode=ro&amp;immutable=1` connection, the
   lock-free reader `_magic` uses, reports `journal_mode=delete` for a WAL file, and `sqlite_dbpage` is not compiled
   into either bundled SQLite (3.50.4, 3.54.1; probe of 2026-11-21) — so the journal mode cannot be read lock-free in
   this process. A fresh process that holds no lock on the file can read the header with a plain descriptor safely.
6. Unlinking a leaked clone costs nothing when the source was unfragmented (0.0002–0.027 s, round 6 A E6) and 33.0 s
   and 39.9 s when the leak was taken from a file fragmented by 40k random page writes and then diverged by 40k more
   (round 6 C e7, e7b). APFS frees the space asynchronously after the unlink returns.
7. Space: while a clone pins the old blocks, a full rewrite of the live file needs 2.0 × the database (copy-on-write
   blocks plus the journal or WAL; measured 390–392 MB peak for a 196 MB database, C e6c), against 1.0 × without a
   clone. During the post-mine check no writer runs on the palace: the mine holds `mine_palace_lock` (cross-process
   `flock` beside the palace) and, in the hub, the shared phase admits readers only (`mine_lock.py:368-369`,
   `mcp_server/http.py:126`). The only planned divergence is the fold of a copied `-wal` into the clone.
8. An orphaned helper keeps a lock it inherited (round 5); its `alarm` self-destruct fails when SIGALRM is ignored or
   blocked in the parent (round 6 C e3: the `flock` was freed after 8.2 s and 8.7 s and the orphan took a clone after its
   parent died; 3.1 s with default signal state). `python -I` still runs the venv's `.pth` files (coverage start-up
   when `COVERAGE_PROCESS_START` is set: 760 ms max before the first line); `-I -S` starts in 37 ms median (B E2).
   The helper itself, idle and under 40 CPU burners on 20 CPUs: 0.083 / 0.27 s worst to `done`; `palace_db_lock` held
   0.068–0.094 s with a 3 GB parent (A E2, C e8).

## Mechanism
`snapshot_database(db_path) -&gt; ContextManager[Snapshot]` in `backends/_inproc_sqlite.py` (the module that owns
in-process SQLite access, its locks and guards).

**Containment rule (one rule for every step below, entry, `quick_check` and exit):** every `Exception` raised by this
machinery is caught, logged once with its traceback at warning level, and turned into a skip member (`SETUP_FAILED`
at entry and exit, `COPY_UNREADABLE` in `quick_check`). `KeyboardInterrupt`, `SystemExit` and other
`BaseException`s always propagate, after the exit clean-up has run. An exception raised by the caller's own block
propagates unchanged; an exit clean-up failure never replaces it.

**Never open the live database outside SQLite in this process.** The parent learns the journal mode from the helper
(premise 5); the helper's own plain reads happen before it opens SQLite, when it holds no lock to lose.

**Entry (inside the caller's exclusive section; bounded, see G12):**
0. `_SNAPSHOTS` false (module flag, also cleared by `MEMPALACE_INTEGRITY_SNAPSHOT=0`) → `DISABLED`. No database
   file → `NO_DATABASE`. A device this process already found unable to clone (cached by `st_dev` from an earlier
   helper answer, see step 4) → `UNSUPPORTED_FS`. When the snapshot directory already exists, the sweep of step 1
   (with its checks) runs before `DISABLED` and `UNSUPPORTED_FS` are returned, so turning the feature off or moving the
   palace never strands a leaked clone; a missing directory is not created for a skip.
1. The snapshot directory is `os.path.join(palace, SNAPSHOT_DIR_NAME)` (`".integrity-snapshots"`, a module constant
   that `backups.copy_palace_dir` and `migrate._carry_over_palace_files` import; nothing else names it). Create it with
   `os.mkdir` and set mode 0700 with `os.chmod` afterwards (the umask masks `mkdir`'s mode, `config.py:481`). It
   must be a real directory owned by this euid (`os.lstat`: not a symlink) → else `UNSAFE_DIRECTORY`; a palace shared
   by several users therefore keeps today's path for every user but the directory's owner. Open `.lock` with
   `palace_lock._open_lock_file(path, 0o600, refuse_links=True)` (no symlink, regular file, one link).
   `flock(LOCK_EX | LOCK_NB)`, retried for at most 0.5 s → `SNAPSHOT_BUSY`. Holding it, **sweep**: `rename` every
   other entry except `trash/` into `trash/` (O(1) per name; premise 6). The trash is emptied after the block (Exit).
   Relation to other locks: `mine_palace_lock` is re-entrant across a whole process and the hub holds it as a lasting
   lease (`palace_lock.py:487-490`), and hub mines are serialized by `_HTTP_MINE_LOCK` (`mcp_server/http.py:188`);
   neither excludes two snapshots in one process. This `flock` is the exclusion between snapshots of one palace, in
   any process, and also stops a helper orphaned by a dead parent from racing the next snapshot.
2. Free space: `f_bavail` ≥ `-wal` size + max(512 MiB, 2 % of the database size) → else `LOW_SPACE` (premise 7: the
   only planned divergence is the fold; the margin covers the hub's readers and clone metadata). A leaked clone still
   in `trash/` counts as used until it is freed; such a mine gets `LOW_SPACE` once and the next one does not.
   Known limit, stated in the docstring: a writer that ignores `mine_palace_lock` and rewrites the whole live file
   during the scan needs 2.0 × the database while the clone exists, against 1.0 × without it.
3. Under `palace_db_lock(db_path)` (one hold covering steps 3 and 4), spawn the helper:
   `[sys.executable, "-I", "-S", "-c", HELPER_SCRIPT, db_path, clone_prefix]` with `pass_fds=(lock_fd,)`, stdin and
   stdout pipes. The script imports only the standard library. First, before anything else: `signal.signal(SIGALRM,
   SIG_DFL)`, `signal.pthread_sigmask(SIG_UNBLOCK, {SIGALRM})`, `signal.alarm(3)`. Then, holding no lock yet, it reads
   the 100-byte header with a plain descriptor (and closes it), refuses a hot journal (a non-empty `-journal` whose
   header is not zeroed → answer `hot-journal`), and answers `mode rollback` (header bytes 18–19 = 1: delete, truncate or persist) or `mode wal` (= 2). The parent:
   - `hot-journal` → kill, reap → `HOT_JOURNAL` ("an interrupted write must be rolled back by a read-write open
     first"; today's path then reports it exactly as today).
   - `mode wal` → establish this module's guard through `_snapshot_guard(db_path, key, budget=2.0)`: if a guard for
     the current inode and wal-index is already holding (macOS: `_holders[key].ready` and not stale; Linux: the anchor
     is guarded), use it; if there is none, start one with the existing `_start_holder` / `_ensure_anchor` code, waiting
     at most the budget; a holder that is stale, not ready within the budget, or failed is **not** replaced here (that
     wait can reach 6 s, `_inproc_sqlite.py:432-449, 497-507`) — it is left to the next `open_reader`, and the snapshot
     returns `WAL_UNGUARDED`. `_snapshot_guard` returns a bool; the snapshot never reads `_holders`/`_anchors` itself.
     A `-wal` without `-shm` (a crash leftover) is guarded like any other WAL palace: the guard becomes the first
     opener, which is today's behaviour for any read of that palace (C e9).
   - then sends `go`, and the helper re-arms `alarm(3)`.
4. The helper sets `timeout=1.5` on its connection (below the parent's deadline and its own alarm), records
   `stat(db_path)` identity, opens the live database `mode=ro` (never read-write, never `_HOLD_SCRIPT`'s sidecar
   rule), runs `BEGIN` + one read (SHARED, or a read mark in WAL), and clones the database, then `-wal` in WAL mode, to
   unique names under the snapshot directory. On macOS the clone is `clonefile(2)` by path. On Linux `ioctl(FICLONE)`
   needs a descriptor on the live file: the helper opens it **before** `BEGIN` and closes it only **after** `COMMIT`
   (premise 5); the Linux path stays disabled until G1 and G3 pass on a btrfs and an xfs reflink loop image. It checks
   the identity again, ends the transaction, prints one line — `done`, `busy` (any `SQLITE_BUSY` / `database is
   locked`), `unsupported &lt;errno&gt;` (`ENOTSUP`, `EOPNOTSUPP`, `ENOTTY`, `EXDEV`, `EINVAL` from the clone call: the
   filesystem cannot clone; the parent caches the device), or `failed &lt;detail&gt;` (including an identity change) — and
   exits. Deadline: 2.0 s from `go` (rollback: from the spawn).
   `done` → release `palace_db_lock`. `busy` or the deadline → **release `palace_db_lock` first**, then kill and reap
   the helper and remove its names → `DATABASE_BUSY`. `unsupported` → `UNSUPPORTED_FS`. `failed` → `SETUP_FAILED`.
5. Yield `Snapshot(skip=None)`. It owns the `flock` and the clone names until `quick_check` or exit.

**`Snapshot.quick_check()` (the caller runs it in `mine_shared_phase`, so nothing here blocks searches):**
a. WAL clone: fold it with a read-write connection on the private clone (`wal_checkpoint(TRUNCATE)`,
   `journal_mode=delete`); require header bytes 18–19 = 1 and no clone sidecar names; any failure, including an
   `sqlite3.Error` such as the measured `disk I/O error` when space runs out (B E4) → `FOLD_FAILED`, return `None`.
b. Open the clone `mode=ro&amp;immutable=1` with plain `sqlite3` (never `open_reader`); unlink every clone name; release
   the `flock`. A failure to open the copy → `COPY_UNREADABLE`, return `None` — a fault of the speed-up, not of the
   palace, so it never becomes a finding.
c. `integrity_errors(lambda: conn)` runs `PRAGMA quick_check` on it. Rows other than `ok` are findings. An
   `sqlite3.Error` / `ValueError` *during the scan of the copy* → `COPY_UNREADABLE`, return `None` (the live check
   then decides). Returns `list[str]`, `[]` meaning clean.

`quick_check()` may be called any number of times inside the block, on the thread that entered it: the first call
computes, later calls return the same value; with a skip set it returns `None` at once. Outside the block or from
another thread → `SnapshotUsageError` (the clone connection is owned by the entering thread).

**Exit (every path after step 1, in `finally`):** kill and reap the helper if it is still alive; close the clone
connection; rename any remaining clone name into `trash/`; release the `flock`; then start one daemon thread that
unlinks the entries of `trash/` (no lock held; it may take tens of seconds for a fragmented leak, premise 6, and runs
outside every exclusive section; whatever it has not finished when the process exits is swept and emptied next time).
The kernel releases the `flock` on any death.

**Logging:** `snapshot_database` logs its own outcome; each `SnapshotSkip` member carries its level —
`DISABLED` debug; `UNSUPPORTED_FS` and `UNSAFE_DIRECTORY` warning once per process per palace (the CLI mine path
drops info records, `palace_lock.py:120-128`); every other member warning with `detail`, every time. Callers log
nothing about skips.

## Interface
```python
# backends/_inproc_sqlite.py
SNAPSHOT_DIR_NAME = ".integrity-snapshots"

class SnapshotSkip(enum.Enum):
    """Why a snapshot gives no verdict. Named by cause, not by mechanism; each carries its log level."""
    DISABLED = ("debug", "snapshots are turned off")
    NO_DATABASE = ("warning", "no database file")
    UNSUPPORTED_FS = ("warning", "the filesystem cannot clone a file in O(1)")
    UNSAFE_DIRECTORY = ("warning", "the snapshot directory is not a private directory of this user")
    SNAPSHOT_BUSY = ("warning", "another snapshot of this palace is in progress")
    LOW_SPACE = ("warning", "not enough free space for the copy to diverge")
    HOT_JOURNAL = ("warning", "an interrupted write must be rolled back first")
    WAL_UNGUARDED = ("warning", "WAL database whose guard is not holding within its time budget")
    DATABASE_BUSY = ("warning", "the database stayed locked past the deadline")
    SETUP_FAILED = ("warning", "the copy could not be made")
    FOLD_FAILED = ("warning", "the copied WAL could not be folded into the copy")
    COPY_UNREADABLE = ("warning", "the copy could not be opened or scanned")

class SnapshotUsageError(RuntimeError): ...

class Snapshot:
    skip: Optional[SnapshotSkip]   # why this snapshot gives no verdict: set at entry, or by quick_check()
    detail: str                    # cause text for a skip, "" otherwise
    def quick_check(self) -&gt; Optional[list[str]]: ...

@contextmanager
def snapshot_database(db_path: str) -&gt; Iterator[Snapshot]: ...

def integrity_errors(open_conn: Callable[[], sqlite3.Connection]) -&gt; list[str]: ...
```

## Interface comments
```python
@contextmanager
def snapshot_database(db_path: str) -&gt; Iterator[Snapshot]:
    """A private, consistent copy of the SQLite database at ``db_path`` for one long read.

    The copy is one committed state of the live file. A short-lived helper process holds a read transaction on the
    live file while it clones it; this process holds ``palace_db_lock`` meanwhile, at most 2 s (rollback) or 4 s
    (WAL, including the guard). In rollback mode that read lock makes every writer of every process wait; in WAL mode
    the read mark keeps the copied database and WAL mutually consistent.

    What each actor writes: the helper never writes the live database, its ``-wal`` or its ``-shm`` beyond the
    read mark any reader takes. In WAL mode this function establishes this module's guard for the file, exactly as
    the first read through ``open_reader`` would; the guard may create ``-wal`` and ``-shm`` and stays after the
    block, as it does after any read. This process never opens the live file outside SQLite.

    Preconditions, stated rather than implied: the caller holds the exclusive section of a mine of this palace (no
    writer runs until its shared phase ends); the snapshot directory lives inside the palace directory and only its
    owner can use it. A writer that ignores ``mine_palace_lock`` and rewrites the whole file during the block needs
    twice the database's size in free space while the copy exists.

    Yields a ``Snapshot``. ``snapshot.quick_check()`` returns ``None`` when no verdict from a copy is available
    (``skip`` then says why; it is already logged), and the caller checks the live file as before. No ``Exception``
    from this machinery leaves this function or ``quick_check``; ``KeyboardInterrupt`` and ``SystemExit`` do. Nothing
    in the block holds a lock on the live database. The copy is freed after the block (its blocks are released
    asynchronously, on a background thread).
    """

def quick_check(self) -&gt; Optional[list[str]]:
    """``PRAGMA quick_check`` of the copy: ``[]`` when clean, else the rows it reported. ``None`` when there is no
    verdict from a copy (no copy was taken, the WAL could not be folded, or the copy could not be opened or scanned);
    ``skip`` says why. Callable any number of times inside the block, on the entering thread; later calls return the
    first result. Outside the block or from another thread: ``SnapshotUsageError``.
    """

def integrity_errors(open_conn: Callable[[], sqlite3.Connection]) -&gt; list[str]:
    """Open with ``open_conn``, run ``PRAGMA quick_check``, return every row that is not ``ok``. An ``sqlite3.Error`` or
    ``ValueError`` from the open or the scan is returned as one finding — the live check's rule (``repair.py:1014-1070``).
    The snapshot catches those itself before calling, because on a copy they are faults of the copy, not findings.
    There is no argument-free form: a verdict always comes from a connection that was opened."""
```

## Data flow — `_validate_palace_fts5_after_mine`
writes done → `_close_chroma_handles` (as today; its reasons are the live fallback and the FTS5 heal, which need no
open client — not the snapshot) → `with snapshot_database(db) as snap: with mine_shared_phase(): errors =
snap.quick_check()` → block exits (copy freed asynchronously) →
- `errors == []` → return.
- `errors is None` → today's code path exactly (`sqlite_integrity_errors` in a shared phase, heal, raise).
- `errors` non-empty → warning with the findings → today's code path; if the live check is then clean, warning
  "snapshot verdict disagreed with the live file" with both verdicts. Only rows from scanning the copy reach this
  branch, never a fault of the copy.
`palace_lock` looks `snapshot_database` up on the `_inproc_sqlite` module at call time: that is the test seam (G8).
The fallback runs after the block, never inside it (inside would pin the copy through a second 256 s scan).
`repair._quick_check_errors` becomes `integrity_errors(lambda: open_reader(path))` plus its named-pipe refusal.

Known difference on the taken-with-findings path (rare: corruption): today the live check starts right after
`_close_chroma_handles`; here it starts after a shared phase in which hub readers may have reopened clients. Heal
safety holds — the second shared phase's re-close still runs before the FTS5 heal. Cost on that path: one extra scan
of the copy (≈ 256 s here) before today's path.

## Information hiding map
| Module | Hidden decision |
|---|---|
| `_inproc_sqlite.snapshot_database` | clone syscall per platform, read transaction and header read in a helper process, WAL guard and fold, snapshot directory and trash, orphan and stale-copy clean-up, free-space rule, skip logging |
| `_inproc_sqlite.SNAPSHOT_DIR_NAME` | the one name walkers must skip (backups, migrate) |
| `_inproc_sqlite.integrity_errors` | how a connection's `quick_check` rows and errors become findings, shared by the copy and the live check |
| `palace._validate_palace_fts5_after_mine` | when a verdict from a copy is enough and when the live file must be checked |

## Watch points
- Never open the live database outside SQLite in this process; in the helper, only before `BEGIN` or with a descriptor
  that stays open until after `COMMIT`.
- Nothing that can take longer than O(1) per entry runs under the `flock` inside the exclusive section: the sweep only
  renames; unlinking happens on the trash thread.
- The fallback stays outside the `with`; the verdict routing stays in the caller, the copy mechanics in the module.
- The helper never opens the live file read-write and never uses `_HOLD_SCRIPT`'s sidecar rule.
- Any new step after the helper is spawned must be inside the `finally` that kills and reaps it.
- The snapshot never replaces a stale guard; that wait belongs to `open_reader`.
- Clones never outlive the block. Clone-based backups (repair, migrate) are out of scope and would need their own
  lifetime and ownership design.

## Gates (written before code)
- G1 Torture, Python writer process, rollback and WAL, ≥ 300 each → 0 inconsistent copies; negative control (no read
  transaction) shows torn copies. On macOS APFS, and on btrfs and xfs reflink loop images before the Linux path is
  enabled (a Linux run that is not done keeps Linux disabled; it is never "recorded as not run" and shipped).
- G2 Torture, Chroma `PersistentClient` writer process, ≥ 1,000 → 0 inconsistent; negative control ≥ 1 torn.
- G3 Torture, Chroma client in this process writing, rollback **and WAL**, ≥ 300 each → 0 inconsistent; on the same
  platforms as G1. Plus a lock-drop negative control: a helper variant that closes a plain descriptor inside its
  transaction lets an external commit through (the order rule of step 4 is what G3 relies on).
- G4 WAL storms (RESTART/TRUNCATE checkpoint loop, `wal_autocheckpoint=10`, a reader on a fully backfilled WAL) → 0.
- G5 Kills and leaks: SIGKILL of the parent during the helper, during the fold, during the scan, during the trash
  thread; SIGKILL of the helper mid-clone; an exception injected between spawn and the helper's answer; the parent
  started with SIGALRM ignored and with SIGALRM blocked; `launchctl kickstart -k` of a test hub mid-scan → no helper
  alive 3.5 s after the parent's last message to it, no later snapshot waits more than 0.5 s for the `flock`, after the
  next snapshot no entry but `.lock` and `trash/` remains and `trash/` empties, and free space returns.
- G6 Live files: the database and `-wal` have the same inode, size, mtime and content hash before and after across
  G1–G5; `-shm` is never created, truncated or replaced by the helper (same inode and size); and **this process opens
  no descriptor on the live database outside SQLite** (`fs_usage`/`dtruss` on macOS, `strace -e openat` on Linux,
  over a snapshot). Fixtures: a hot rollback journal (`HOT_JOURNAL`, journal and database byte-identical after);
  `-wal` without `-shm` (guard established, snapshot taken, copy correct); a quiesced WAL palace in a CLI-style
  process (the module guard is established as for any read, the snapshot is taken, the sidecars afterwards are the
  guard's).
- G7 Every `SnapshotSkip` member produced by a fixture and logged at its level, and the mine then follows today's path:
  flag off (and `MEMPALACE_INTEGRITY_SNAPSHOT=0`); no database; an HFS+ image (`unsupported` from the clone call, then
  cached: the second mine spawns no helper); a symlinked and a foreign-owned snapshot directory
  (`UNSAFE_DIRECTORY`, the symlink target untouched); a second holder of `.lock`; low space; WAL with a stale holder
  (`WAL_UNGUARDED` within the budget); a writer holding the database past 2 s (`DATABASE_BUSY`, never
  `SETUP_FAILED`); a helper crash; a database replaced during the clone; a `-wal` copy removed before the fold; a fold
  that raises `sqlite3.Error` (`FOLD_FAILED`); a copy that cannot be opened and one whose scan raises
  (`COPY_UNREADABLE`, no disagreement warning); an exception injected into each exit clean-up step (the mine
  succeeds, a body exception is not replaced); `KeyboardInterrupt` injected at entry (propagates, clean-up ran).
- G8 Routing through the seam, on every platform: clean → no live check; findings → today's path; `None` → today's
  path; disagreement warning; `quick_check()` twice returns the same value; `SnapshotUsageError` after the block and
  from another thread. The same routing with a real copy on APFS (and btrfs/xfs where enabled).
- G9 No copy key in `_anchors`, `_holders` or `_locks` after a snapshot; the live file's macOS holder (same pid) and
  Linux anchor are unchanged by a snapshot; a stale holder is still there (not replaced) after a `WAL_UNGUARDED`.
- G10 `copy_palace_dir` and the carry-over of palace files skip `SNAPSHOT_DIR_NAME` (imported, not spelled).
- G11 Space on a 200 MB APFS image: below the threshold → `LOW_SPACE`; a leak in `trash/` is renamed before measuring;
  at the threshold, a fold of a `-wal` of the threshold's size completes, and the live file sees no `SQLITE_FULL` from
  the writes the design admits during the block; a full rewrite by a writer that ignores `mine_palace_lock`, at 1.5 ×
  the database free, is recorded as the stated limit (its outcome printed, not asserted away).
- G12 Bounds under a busy in-process Chroma writer, 300 snapshots per mode: `palace_db_lock` held ≤ 2.05 s (rollback)
  and ≤ 4.05 s (WAL, including the guard, with a stale holder present); the whole entry of `snapshot_database` ≤ 5 s
  p100, **with a leaked clone taken from a file fragmented by ≥ 40k random page writes and then diverged by ≥ 40k more
  present in the snapshot directory**; no helper alive more than 3.5 s after the parent's last message; the trash
  thread's unlink of that leak measured and printed.
- G13 Upstream suite green apart from doc 06's two known failures; an autouse fixture pins `_SNAPSHOTS=False` except in
  the snapshot tests, which include one APFS integration test with the flag on in the default suite (skipped with a
  printed reason off APFS); Linux runs where available (ext4 → `UNSUPPORTED_FS`; btrfs/xfs loop images → G1 and G3
  first).
- G14 Live hub: a 20-file mine with concurrent searches; the hub log shows the snapshot was taken; during the check
  every search's lock wait ≤ 5 s and run ≤ 2× its idle median; no `rlock_acquire` stall in a hub sample;
  `repair-status` OK; `.integrity-snapshots/` holds only `.lock` and an empty `trash/` a minute after the mine.

## Review history
- **Round 6 → revision 5** (A, B, C: conditional pass; 3 high, separate roots). Folded in:
  - the parent's header read through a plain descriptor drops this process's POSIX locks (A1, measured: a second
    writer got RESERVED, a live `-wal` was unlinked), and Linux `FICLONE` needs a descriptor in the helper whose close
    drops its SHARED (C5, measured) → the helper reads the header before it holds any lock and reports the mode; the
    Linux descriptor stays open until after `COMMIT`; G6 traces opens; Linux stays off until G1/G3 pass on reflink
    images;
  - the sweep unlinked a leak inside the exclusive section, 33–40 s for a fragmented leak (C1) → rename into
    `trash/`, unlink on a background thread after the block; G12's fixture is a fragmented, diverged leak;
  - exceptions from the fold and from exit escaped "never fails a mine", and the `BaseException` policy was unstated
    (B4, A7) → one containment rule; `FOLD_FAILED`/`COPY_UNREADABLE`; G7 fixtures for each;
  - the guard bound could reach 6 s (A3, C8, B6) → `_snapshot_guard` with a 2 s budget that never replaces a stale
    guard; one `palace_db_lock` hold for steps 3–4;
  - the 1.1 × free-space rule was wrong both ways and disabled the feature at ≥ 46 GB on this disk (C2, C3) → sized to
    the fold plus a margin, the full-rewrite case stated as a limit;
  - skips at step 0 never swept leaks (C4) → sweep first;
  - the snapshot directory leaked into backups/migrate, its modes ignored the umask and contradicted each other, and
    the sweep followed a symlink (A5, A6, B7, C10) → `SNAPSHOT_DIR_NAME`, 0700 set after `mkdir`, owner and
    `lstat` checks (`UNSAFE_DIRECTORY`), `.lock` through `_open_lock_file(refuse_links=True)`;
  - `integrity_errors()` without arguments returned clean and left each caller its own `except` (B3) →
    `integrity_errors(open_conn)`;
  - faults of the copy were reported as findings (B5) → `COPY_UNREADABLE`, `None`;
  - docstring promises were false for the WAL guard (B6) → promises stated per actor, the guard named as a side effect;
  - the `mine_palace_lock` relation was misstated (A2) → `_HTTP_MINE_LOCK` named, the `flock` stated as the exclusion;
  - the filesystem allow-list depended on ctypes ABI offsets and admitted XFS without reflink (A4) → the clone call's
    errno decides, cached per device;
  - the helper ran `.pth` start-up code before its alarm and an ignored/blocked SIGALRM defeated it (B10, C6) →
    `-I -S`, SIGALRM reset and unblocked first; the helper's busy timeout is 1.5 s (A8);
  - refusing `-wal` without `-shm` bought nothing (C7) → guarded and snapshotted; a hot journal has its own cause
    (C9); `UNSUPPORTED_FS` is a warning once per process (B11);
  - the caller protocol had rules that could be defined away (B1, B2) → `quick_check()` always callable inside the
    block, idempotent; `skip` documented as set at entry or by `quick_check`; the client-close rationale moved to the
    caller (B8); clone lifetime decided (C11, B9): clones never outlive the block; backups out of scope.
  - Held under experiment, no change: Chroma writers wait on the helper's SHARED and succeed (B E1); the helper's
    latency and the 2 s deadline (A E2, C e8); the WAL fold reproduces the live rows (C e9); a hot journal and the live
    files stay byte-identical (C e1).
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