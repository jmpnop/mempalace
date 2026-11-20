# 07 — Which parts of MemPalace would gain from a Rust port? (measured)

**Status:** analysis complete; no code changed. Decision on what to build is open.

## Question
Move performance-critical functionality from Python to Rust: which parts of the code need porting?

## Method
- An APFS clone (`cp -c`) of the asgard palace (6.9 GB `chroma.sqlite3`, 1,166,966 drawers, `repair-status` OK), driven
  in-process by the same interpreter, fork commit and patched chromadb wheel the hub runs. The live hub was not touched.
- Workloads through the real MCP tool handlers: 6 wing-scoped searches, 3 palace-wide searches, 5 `add_drawer`,
  5 `get_drawer`, an overview set (`status`, `list_wings`, `get_taxonomy`, `list_drawers`, `status` again,
  `graph_stats`), 3 `kg_query`, 5 `delete_drawer`, and a 20-file mine into a throwaway wing.
- Run 1 used cProfile plus a 5 ms watchdog thread. On Python ≥ 3.12 cProfile instruments every thread through
  `sys.monitoring`, so the watchdog's own sleeps polluted its per-function split (they showed up under unrelated
  callers). Run 1's wall times and its interpreter-lock stall measurements stand; its function split is discarded.
- Run 2 used py-spy 0.4.4 (built from crates.io), attached per workload to the running harness, sampling at 50 Hz with
  idle frames included. Coverage matched wall time for every workload except the cold search, where py-spy fell behind
  while the process was inside long native calls (106 samples in about 30 s).

## Wall times (run 2; run 1 in brackets where it differs)
| Workload | Time |
|---|---|
| first search, cold process | 17.9 s [73.4 s with cold OS caches and profiler overhead] |
| warm wing-scoped searches | 1.6–4.4 s |
| warm palace-wide searches | 2.2–3.2 s |
| `status` first / second call | 22.0 s / 1.7 s |
| `graph_stats` | 23.2 s |
| `list_drawers` | 3.4 s |
| `add_drawer`, `get_drawer`, `kg_query`, `delete_drawer` | under 0.1 s each |
| 20-file mine | 221 s [271 s] |

## Where the time goes (py-spy, main thread)
### Mine (11,164 samples ≈ 223 s)
| Share | What | Kind of work |
|---|---|---|
| 71.8 % | `repair._quick_check_errors` → `PRAGMA quick_check` (post-mine validation) | native SQLite B-tree walk |
| 13.5 % | `mined.prefetch_complete_mtimes` → `_sqlite_iter_metadata`: every drawer's metadata, to skip already-mined files | SQLite row fetch + Python per-row dict building |
| 11.8 % | `process_file`: chunking, entity detection, embedding (ONNX 3.1 %), Chroma get/upsert/update/delete | mostly native (ONNX, Chroma's Rust bindings) |
| ~1 % | hallway computation, closets, misc. Python | Python |

### Overview (2,716 samples)
| Share | What | Kind of work |
|---|---|---|
| 42.7 % | `graph_stats` → `sqlite_room_wing_hall_counts` (one GROUP BY with 4 LEFT JOINs) | native SQLite |
| 38.1 % | `status` → `_sqlite_wing_room_counts` (GROUP BY with 2 LEFT JOINs + bookkeeping filter) | native SQLite |
| 7.3 % | `hnsw_capacity_status`: pickle load of HNSW metadata + element count | native unpickler + SQLite |
| 5.8 % | `list_drawers` → `sqlite_wing_names` | native SQLite |

### Palace-wide search (527 samples, 32 % of them the harness's own wait between steps)
| Share of search time | What | Kind of work |
|---|---|---|
| ~72 % | closet boosts → `sqlite_lexical_rows`: FTS5 `MATCH` over `embedding_fulltext_search` | native SQLite (FTS5) |
| ~25 % | `hnsw_capacity_status` (pickle load 9 %, count 7 %) | native |
| ~3 % | Chroma vector query, ONNX query embedding, BM25 scoring, tokenizing | native + a little Python |

### Cold client open (run 1, interpreter-lock watchdog)
- chromadb's `RustBindingsAPI.start` held the interpreter lock for **44.9 s** in one call: every other thread in the
  process (the hub's HTTP handlers, `/healthz`) is frozen for that long. That is chromadb's own Rust code; the local
  chromadb patch (doc 01) released the lock only around add/update/upsert/delete/count, not `start`.
- `quarantine_invalid_hnsw_metadata` walks millions of pickled HNSW dictionary entries in pure Python (run 1 cProfile:
  3.9 M `dict.get` calls and 3.5 M generator steps in `_missing_dimensionality_appears_recoverable`, ≈ 3.4 s of own
  time, inflated by cProfile). This is the one sizeable pure-Python CPU loop found.

## Can the SQLite-bound parts be made faster without Rust? (tested on the clone)
- The two count queries run 19.0 s and 22.5 s **warm**, with a sensible plan: per drawer, 2–4 lookups through the
  `(id, key)` unique index plus a table-row fetch for the value (≈ 6–12 M random B-tree probes).
- A rewrite that reads `embedding_metadata` once in index order and pivots per id returned **identical** groups (152)
  in 24.7 s — no gain: the cost is fetching ≈ 5.85 M table rows whose values no index covers. A Rust reader would
  fetch the same rows through the same kind of B-tree.
- `status` already has a cache (second call 1.7 s); `graph_stats` has none.

## Verdict: what a Rust port would and would not buy
| Hot path | Measured cost | Bound by | Rust port helps? | What does help |
|---|---|---|---|---|
| post-mine `quick_check` | 159 s per mine on this palace (72 %) | native SQLite | **No** — same B-tree walk | not blocking on it: check an O(1) clone (design Revision 3) |
| `status` / `graph_stats` counts | 19–23 s warm | native SQLite row fetches | **No** | maintained or cached aggregates invalidated by the palace's write stamp (`status` has one; `graph_stats` lacks it) |
| search closet boosts (FTS5) | ~72 % of a 2–3 s search | native FTS5 | **No** | cap/scope the closet FTS query; cache per query |
| `hnsw_capacity_status` | ~25 % of a search, 7 % of overview | native unpickle + count | marginal | a stamp-keyed cache (the uncached path `_hnsw_capacity_status_uncached` ran in these calls) |
| `prefetch_complete_mtimes` | ~30 s per mine (13.5 %) | SQLite fetch + Python per row | partly (the Python half) | **scope the scan to the files being mined** on the Chroma backend, as it already is for `sqlite_exact` (O(files) instead of O(palace)) |
| HNSW metadata quarantine on cold open | ≈ 3.4 s (cProfile) | **pure Python** | **Yes** — the only real candidate | or vectorize / skip re-validation of unchanged files |
| chromadb `start` holding the GIL | 44.9 s cold | chromadb's Rust | n/a (already Rust) | release the GIL around `start` in the local chromadb patch |

## Constraint on any Rust code that reads `chroma.sqlite3`
The existing workspace crates (`mempalace-core`, `mempalace-py`, `mempalace-cli`) link rusqlite with the `bundled`
feature: a **third** SQLite library in the hub process. `backends/_inproc_sqlite.py` exists because two libraries in one
process cannot see each other's POSIX locks (a close in one drops the other's locks; reads can tear against a commit).
A Rust reader of `chroma.sqlite3` would sit outside `palace_db_lock` and the anchor/holder machinery unless it links
the *same* SQLite as Python, or runs out of process. Rust code that does not touch SQLite (the quarantine walk over
pickled HNSW metadata) does not have this problem.

## Recommendation
Port to Rust only the pure-Python CPU loop (HNSW metadata quarantine, ≈ 3.4 s per cold open). Everything else
that dominates the measured time is native SQLite or chromadb work; it needs algorithmic changes (scope the mined-file
scan, cache aggregates and the capacity probe, check a clone instead of the live file) and one chromadb patch
(release the interpreter lock in `start`). Each is its own design with gates.
