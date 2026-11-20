# 03 — Every MCP save took 21–47 s (MemPalace 3.10.0; fixed upstream in 3.12.0)

**Status:** investigated and fixed locally on 3.10.0 (Design A below, live-verified), then **removed** by the upgrade
to 3.12.0, which ships its own fix. One rejected design is recorded so it is not reintroduced.
**Artifacts:** [`../patches/mempalace-3.10.0/02-session-single-cache.DESIGN.md`](../patches/mempalace-3.10.0/02-session-single-cache.DESIGN.md)
(POSD Design-It-Twice), [`02-session-single-cache.apply.py`](../patches/mempalace-3.10.0/02-session-single-cache.apply.py)
(applies the patch to an installed 3.10.0 + patch 02 tree), gates [`../gates/session-single-cache/`](../gates/session-single-cache/).

## Measurement
Flag-gated `cProfile` at the hub's tool dispatch, then wall-clock timers per phase (temporary, removed afterwards).
A warm save (client side ~21 s):

| Phase | Time |
|---|---|
| `_get_collection` → `_session._get_client`: `_force_chroma_cache_reset` 1.05 s + `ChromaBackend.make_client` 9.45 s (`mtime_changed=True`) | 10.5 s |
| first `col.get(ids=[…])` on the fresh client (reloads the collection); the read-back `get` right after: 0.00 s | 7.3 s |
| the upsert itself | 0.06 s |

Cause: the MCP session kept its **own** chromadb client cache (`_get_client`, baseline `_palace_db_inode/_mtime`)
that never re-baselined after the hub's own writes, so the next call saw its own write as an external change and
rebuilt the client (incl. `quarantine_invalid_hnsw_metadata`). `ChromaBackend` had already fixed this for its cache
(`_restamp`, "a cache that essentially never hit"); the session cache had not. Searches (~6.4 s) were a separate cost:
`hnsw_capacity_status` recomputed (3.4 s) + the query (2.6 s).

## Rejected design: process-global "own stat" (POSD adversarial review: FAIL ×3)
A shared record of "the last `(inode, mtime)` this process produced", consulted by both caches. Three independent
reviewers (complexity / interface / durability) each found the same critical flaw, verified in code: the hub has
**two** client caches over one palace; `ChromaBackend._drain_clients` closes only its own clients and
`_clear_chroma_system_cache` "replaces Chroma's system and refcount maps without calling `System.stop()`", while the
session's reset does close the backend — asymmetric. With a per-process "own" record, the backend's rebuild after an
external change (or after every hub mine) recorded the new stat as "own", so the session kept a stale/orphaned client,
even across an inode replacement: two chromadb Systems persisting divergent HNSW to one palace (the #2002 data-loss
class). Never deployed. Required direction: one client cache.

## Local fix: Design A — "one store, re-ask every call"
- `_session._get_collection` (signature unchanged for its 28 callers): the Chroma branch asks
  `palace.get_collection(...)` on every call, exactly like the non-Chroma branch; `_get_client`, `_client_cache` and
  `_palace_db_*` are deleted. `ChromaBackend.get_collection` already does EF resolution, the get-vs-create split
  (#1262), `_hnsw_creation_metadata` (identical to the session's hand-built metadata), `_pin_hnsw_threads` and
  `_restamp`.
- `ChromaBackend.reset_palace(palace)`: the one reset entry point (drain + clear shared System); used by
  `_force_chroma_cache_reset` and `mempalace_reconnect` (both still refuse/skip while a mine runs).
- Overview caches are no longer cleared on every open (5 s TTL; tools clear them after their own writes).

| Gate | Result on 3.10.0 |
|---|---|
| Negative control: 5 saves on the unpatched hub | 38.6 42.1 23.5 31.9 47.1 s, 5 client opens → FAIL |
| Q1 in-process on a throwaway palace: read-back; warm `_get_collection` cost; inode replacement → next save in the NEW file; touch → search → save; reconnect refused during a mine | OK — warm `_get_collection` avg **0.0032 s** |
| Q2 five saves ≤ 6 s | cold 19.2 s, then **3.6 4.0 3.7 3.8 s**, 1 client open → FAIL as written (the gate did not exempt the first cold open) |
| Q3 save/search interleaved | OK — saves 3.5–3.6 s |
| Q4 external change seen first by a search → next save on a rebuilt client, reads back | OK |
| Q5, Q6 | not run — superseded by the upgrade decision |

## Fixed upstream in 3.12.0
`_session._restamp_palace_db()` re-baselines after the session's own writes, and
`_drop_session_client_for_system_reset()` (registered via `register_before_system_cache_reset`) closes the session
client before the shared System is dropped — which also closes the cross-cache gap the review found. Measured on asgard
after the upgrade: cold save 20.1 s, warm saves **3.5 s** (gate U2, see [04](04-upgrade-to-3.12.0.md)).
