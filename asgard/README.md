# asgard — local MemPalace changes, investigations and evidence

This directory documents every change made to the MemPalace installation on the asgard workstation (Intel Mac,
macOS 15.8, a single hub — `mempalace serve` under launchd `com.mempalace.hub` on 127.0.0.1:8765 — serving a
7.2 GB / 1.17 M-drawer Chroma palace to several agent sessions), why each change was made, how it was measured, and
what replaced it. Branch `asgard-local-patches` of this fork starts at upstream tag **v3.12.0**. Code changes on top
of v3.12.0 are separate commits, each with its own document; the hub runs a byte-identical install of this branch.

## Timeline (2026-11-20)
| # | Problem | Where the fix lives now | Doc |
|---|---|---|---|
| 01 | chromadb 1.5.9 write bindings hold the GIL; a mine froze the whole hub (`/healthz`, MCP connects) | local chromadb rebuild, still installed on 3.12.0 | [docs/01](docs/01-chromadb-writes-hold-the-gil.md) |
| 02 | a mine held the hub's exclusive lock for its whole run (3.10.0) | local 3.10 patch → **superseded** by upstream 3.12.0 (yielding write lock) | [docs/02](docs/02-hub-mine-lock-3.10.md) |
| 03 | every MCP save took 21–47 s: the session client was rebuilt after the hub's own writes (3.10.0) | local 3.10 patch (one client cache) → **superseded** by upstream 3.12.0 (`_restamp_palace_db` + reset hook); one rejected design recorded | [docs/03](docs/03-slow-saves-3.10.md) |
| 04 | upgrade 3.10.0 → 3.12.0 | installed; gates U1, U2, U4 OK, U3 FAIL | [docs/04](docs/04-upgrade-to-3.12.0.md) |
| 05 | still open on 3.12.0: post-mine quick_check blocks for ~10 min; searches 13–27 s; Rust-port plan | — | [docs/05](docs/05-open-issues.md) |
| 06 | the post-mine check blocks readers on the hub lock, then on `palace_db_lock` | commit `ef89c2cb` (hub lock: shared phase) deployed; `palace_db_lock` still serializes readers — open | [docs/06](docs/06-post-mine-check-and-palace-db-lock.md) |
| 07 | which code would gain from a Rust port (measured on a palace clone) | analysis only: nearly all hot time is native SQLite/chromadb; one pure-Python loop qualifies | [docs/07](docs/07-rust-port-analysis.md) |

## Layout
- `docs/` — one document per change: symptom, measurements, cause, change, evidence, status.
- `patches/chromadb-1.5.9/allow_threads.patch` — the chromadb change (applies to the 1.5.9 sdist / tag).
- `patches/mempalace-3.10.0/` — the two 3.10.0 patches (historical; not applied to v3.12.0): `01-hub-mine-lock.patch`,
  `02-session-single-cache.apply.py` + its POSD design document.
- `gates/` — the acceptance gates exactly as run (made portable: test-mine material comes from `$MINE_SRC`, a git
  checkout; probes are written to and removed from the throwaway wing `zz-hub-probe`). Each `GATES*.md` lists the
  gates; each script prints `<ID>_OK` / `<ID>_FAIL`. They talk to the live hub through `mp_call.py`, which drives
  the same `mempalace-mcp` stdio proxy that MCP clients use.

## Method
Every change was gated before it was trusted: gates written first, a negative control run against the unfixed state,
then the live hub measured. Designs that failed review are documented with the reason (docs/03). Measurements quoted
in these docs are the measured values from the gate runs, not estimates.
