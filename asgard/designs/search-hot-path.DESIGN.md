# Design: search hot path — wing names, capacity verdict, closet boosts

**State:** Revision 4 (2026-11-21), after three 3-agent adversarial review rounds (history at the end). Round 3
returned CONDITIONAL PASS from all three reviewers; every required change is folded in below. This document states
only the current design; superseded proposals are summarised in the review history, not left in the body.

## Problem (measured)
Searches on the asgard hub take 2–34 s with zero lock wait. A py-spy profile of the live hub during six real searches
(84.6 s sampled, matching the 85.6 s of the hub's `run=` times):

| Share | Code path | Measured cause |
|---|---|---|
| 34.7 % | `search_util.build_where_filter → … → chroma_known_wings → chroma_sqlite.sqlite_wing_names` | the known-wings cache lives 30 s; on a miss the first query `SELECT MIN(string_value) FROM embedding_metadata WHERE key = 'wing'` cannot use the partial index `embedding_metadata_string_value (key, string_value) WHERE string_value IS NOT NULL` → table scan, 3.3–9.3 s on the palace clone |
| 22.9 % | Chroma vector query | the actual search; out of scope |
| 22.3 % | `searcher.query._closet_boosts → ChromaCollection.lexical_search → chroma_sqlite.sqlite_lexical_rows` | one trigram FTS5 `MATCH` over the index shared by drawers and closets (≈ 1.17 M documents; 56,061 closets). Palace-wide: ranked, 0.4–2.6 s. Scoped by a `where`: no ranking, the unbounded MATCH + `_decoded_rows` + `matches_where`, 0.02–1.4 s warm (≈ 1.04–1.14 s for the closet-less wings in round-3 measurements) — paid in full even when the scope holds no closet (24 of 31 wings, incl. `mempalace`, which 3 of the 6 profiled searches use) |
| 19.5 % | `_guards._refresh_vector_disabled_flag → hnsw_capacity_status → _hnsw_capacity_status_uncached` | the verdict cache is keyed by an `(inode, mtime_ns, size)` fingerprint but also expires after `_CAPACITY_CACHE_MAX_AGE_SECONDS = 10.0`; searches > 10 s apart re-probe (`COUNT(*)` + unpickle, ≈ 2.2–3 s) on an unchanged palace |

Goal: remove recomputation that is not needed, without changing any search output and without holding any SQLite
lock longer than today. Callers: every MCP search, duplicate check, status path. Hidden from callers: query plans,
cache policy, the skip rule.

## Fix 1 — `sqlite_wing_names` uses the partial index
The first query becomes `SELECT MIN(string_value) FROM embedding_metadata WHERE key = 'wing' AND string_value IS NOT
NULL`. `MIN()` ignores NULLs, so the result is identical by definition; plan `USING COVERING INDEX
embedding_metadata_string_value`, 0.0004–0.001 s per query (verified by three reviewers: identical on the clone and on
a synthetic table mixing int/float/bool/string wings; no other query in the package has this form). The whole walk
(one query per distinct wing) is measured by G1, which requires a median below 0.05 s; `_KNOWN_WINGS_TTL` (30 s) is
unchanged.

## Fix 2 — capacity verdict reuse
### Rule
A cached verdict is reused while all of these hold:
1. its fingerprint (sqlite + `-wal`, segment pickle, segment header) is unchanged — as today;
2. the **reuse window** has not elapsed (monotonic clock, as today): 3600 s when the palace's filesystem is
   allow-listed, otherwise 10 s (today's value); 10 s whenever the verdict has no HNSW element count
   (`hnsw_count is None`: it then reads files the fingerprint does not cover);
3. the **age deadline**, if the verdict has one, is in the future (wall clock).

### Age deadline (computed in `hnsw_capacity_status` from public fields only)
The probe's verdict can change with time alone in exactly one case: `status == "ok"`, no explicit sync threshold,
`divergence > 0`, and the pickle younger than `_HNSW_PERSISTENT_DIVERGENCE_GRACE_SECONDS` (300 s) — at pickle mtime +
300 s it becomes "diverged" with no file changing. Whether the threshold is explicit is not in the public dict, so the
caching layer uses a superset that needs only public fields:

  a verdict has an age deadline when `status == "ok"`, `divergence > 0`, and `hnsw_metadata_age_seconds < 300`;
  the deadline is `flips_at = pickle_mtime_ns / 1e9 + 300`, with `pickle_mtime_ns` taken from the post-probe
  fingerprint (`after[1][1]`, the same pickle the probe read; the cache stores only when `before == after`).

An explicit-threshold palace with a young pickle therefore re-probes once at `flips_at` although its verdict cannot
flip; that costs one probe and is never wrong. `_hnsw_capacity_status_uncached` and its return value are unchanged;
no private channel exists.
Clock use: the deadline is wall clock because the probe's own predicate is (`time.time() - getmtime`); the reuse
window stays monotonic so a backward clock step cannot extend it. `time.monotonic` stops during system sleep on macOS
(measured); a verdict reused after a sleep is still guarded by the fingerprint and the wall-clock deadline.

### The age field on a reused verdict
A reused verdict is the probe's dict, copied: `hnsw_metadata_age_seconds` is the pickle's age **at probe time**, and
the docstring says so. Recomputing it on a hit was considered and rejected: no consumer reads the field (only
`tools_read` exposes counts), and identical repeated calls returning identical dicts is pinned by the upstream test
`test_unchanged_palace_probes_once`. Time-dependence of the verdict itself is handled by the age deadline above.

### Filesystem allow-list
`_fs_type(path) -> str` reads `f_fstypename` with `statfs`, called once per stored verdict (microseconds; no cache,
so a volume remounted under a reused device number cannot inherit a stale type):
- macOS: libc symbol `statfs$INODE64` if present, else `statfs` (no `platform.machine()` test, so Rosetta and arm64
  pick the symbol libc really has; the plain `statfs` symbol on x86_64 is the legacy 32-bit-inode ABI and returns an
  empty name — measured on asgard). Struct `statfs64`: `f_fstypename` at offset 72, size 2168; a fresh buffer per
  call.
- any other platform, or any error → `""` (not allow-listed).
Allow-list: `{"apfs"}` (measured: 0 repeated signatures in 2,000 commits; HFS+ and exFAT repeat 1,998/2,000 and stay at
10 s). Linux and other platforms keep today's 10 s — no change for them.

### Logging
Every uncached probe logs one INFO line `hnsw capacity probe: <collection> reason=<first-call|fingerprint|window|
deadline|uncacheable>` (a few per hour on an idle asgard hub). G10 counts these lines.

### Changes in code and tests (`backends/chroma.py`, `mcp_server/_guards.py`, `tests/test_hnsw_capacity.py`)
- cache tuple gains `flips_at: Optional[float]` and `window_s: float`; annotation and comment updated;
- `hnsw_capacity_status` docstring: freshness = fingerprint + reuse window + age deadline (the sentences "rather
  than a wall-clock TTL" and "re-running the probe costs milliseconds" are corrected — it costs 2–3 s on asgard);
  the `_CAPACITY_CACHE_MAX_AGE_SECONDS` comment ("never fires in practice") is corrected; the `_guards.py` comment
  on the 10 s ceiling is updated;
- `test_verdict_is_not_reused_past_the_age_ceiling` is split, and new tests added — the full list is in G2/G3.

## Fix 3 — skip the closet full-text query when the scope holds no row of the collection
### Placement and rule (in `chroma_sqlite.sqlite_lexical_rows`, for any collection)
Inside `read(conn)`, before the unchanged MATCH path, as standalone statements in autocommit (no transaction spans
the check and the MATCH — round 3 measured that one does: a 69 s SHARED hold, writer commits 805 → 20 and three
"database is locked" errors in another process):
1. `eqs = _string_equalities(encode_where(where))` — values encoded exactly as stored (escape prefix); `None` (any
   `$or`, `$ne`, `$nin`, mixed or numeric values, empty `$in`) or an empty list → today's path.
2. For each equality, a **bounded walk** of at most `_SCOPE_PROBE_LIMIT` (20,000) index entries of
   `embedding_metadata (key, string_value IN values)`, in two statements:
   - `SELECT EXISTS (SELECT 1 FROM (SELECT id FROM embedding_metadata WHERE key = ? AND string_value IN (…) AND
     string_value IS NOT NULL LIMIT ?) w JOIN embeddings e ON e.id = w.id JOIN segments s ON s.id = e.segment_id
     JOIN collections c ON c.id = s.collection WHERE c.name = ?)` → hit;
   - only on a miss: `SELECT count(*) FROM (SELECT 1 FROM embedding_metadata WHERE key = ? AND string_value IN (…)
     AND string_value IS NOT NULL LIMIT ?)` → `< limit` means the walk was exhausted, `= limit` means it stopped.
3. The skip rule: return `[]` **iff some equality's walk was exhausted with no hit**. Otherwise (every equality hit,
   or some walk stopped at the limit) → today's path.
4. Any `sqlite3.Error` in the walk → today's path. `sqlite_lexical_rows` reads without a busy timeout (today), so a
   locked database fails the walk at once; the MATCH that follows fails the same way today's does. No added wait.
5. Outcome counters (`skip`, `hit`, `limit`, `error`) in a module-level `collections.Counter`; the first `limit`
   outcome per `(collection, key, values)` in a process logs one INFO line, so a wing that outgrows the limit is
   visible.

Correctness: the check is linearizable on its own — "exhausted, no hit" is true at the instant of the walk, and
today's code would return `[]` for that state: every path (ranked MATCH, unranked MATCH, the recency fallback for
queries without a 3+-character token, the FTS-error fallback) ends in `_decoded_rows(where)`, which keeps no row of an
empty scope. A row inserted after the walk is a concurrent write that today's MATCH could equally have missed.
Verified: 0 mismatches in 362 cases (round 3: closets × 31 wings × 6 queries, palace-wide, `$and`, `$in`, a missing
wing, numeric, backslash value, drawers × 4 scopes; synthetic palace: 600 k-row closet-less wing past the limit,
NUL-escaped value, int 7 vs string "7", `$in`, `$and` with an empty room). Callers (`_lexical_search_via_sqlite` ←
`_closet_boosts`, union-mode drawer fetch) cannot distinguish the paths. `_closet_boosts` is unchanged.

Cost and limit: a walk costs 0.02–0.06 ms on an early hit and 0.0015–0.007 s for the clone's closet-less wings
(1–909 rows); a full walk costs ≈ 0.8–1.6 µs per index entry (600,000 entries: 0.46–0.97 s), so the 20,000-entry
limit bounds the worst case near 16–32 ms against ≈ 1 s saved per skipped query. A limit of 2,000 (revision 3) would
lose the skip as soon as a closet-less wing (today up to 909 rows) passed 2,000 drawers. G4 measures the walk at the
limit and requires it below 0.05 s.

### Rejected alternatives (with the measurements that decided)
- **One read transaction around check and MATCH (revision 3):** SHARED held up to 69 s on a scoped union read
  (MATCH 1.5 s + decode 59 s over 1,019 chunks); a separate-process writer fell from 805 to 20 commits with 3 lock
  errors; on the closet mix writer p50 rose 7×. It bought no correctness (the check alone is linearizable).
- **COUNT instead of EXISTS:** up to 1.9 s on the largest wing.
- **`_equality_sizes`** (revision 1): counts every collection, never 0 for a wing with drawers.
- **Per-collection cache of closet wing names behind the sqlite fingerprint:** the hub writes `chroma.sqlite3` on
  every save, so the cache would be rebuilt (a scan of the collection's metadata) on most searches; the bounded walk
  is cheaper and stateless.
- **FTS restricted by `rowid IN (scope)`:** identical ranks, 27–1,382 s.
- **Wing filter pushed into the MATCH SQL:** identical ids, slower (2.4–7.4 s).
- **Filter-driven read of the scope (`_filtered_candidate_rows`, "3D"):** ineligible for every wing with closets (its
  size check counts all collections: 31 k–219 k rows); when forced, slower (0.17–3.4 s vs 0.04–0.88 s); its candidate
  order changes BM25 tie-breaks (final boosts differ in 9–11 of 15–36 cases); its `str.lower()` substring test differs
  from FTS5 trigram folding on Greek final sigma.
- **In-memory whole-word closet index:** 0.6–6 ms per query, but changes which closets rank (whole words vs trigrams)
  and costs ≈ 19 s per rebuild — a ranking decision left to the operator, outside this design.

## Interface comments
```python
def sqlite_wing_names(palace_path: str, limit: int = 10_000) -> Optional[list[str]]:
    """Every distinct string ``wing`` in ``chroma.sqlite3``, or ``None``.

    Steps through the ``(key, string_value)`` index one value at a time. Every query names ``string_value IS NOT
    NULL``: the index is partial, and without that condition SQLite scans the whole metadata table instead
    (seconds on a million-drawer palace)."""

def _fs_type(path: str) -> str:
    """The filesystem type name of ``path`` (``"apfs"``, ``"hfs"``, ``"exfat"``, …), or ``""`` when it cannot be read
    or the platform has no such name. Only macOS reports names; elsewhere this returns ``""``."""

# hnsw_capacity_status: "A cached verdict is reused while its files are unchanged, within a reuse window (10 s, or
# 3600 s on filesystems whose timestamps change on every write — today APFS), and — for an 'ok' verdict with a
# divergence and a pickle younger than the 300 s grace — until the grace ends. A reused verdict reports the pickle
# age measured when it was probed."

# sqlite_lexical_rows: "A filter naming a value that no row of this collection holds can match nothing. That is
# established first, by a bounded index walk in its own statement, and the full-text query is then skipped. A walk
# that reaches its limit or fails falls back to the full query; no transaction spans the two."
```

## Information hiding map
| Module | Hidden decision |
|---|---|
| `chroma_sqlite.sqlite_wing_names` | index-only enumeration of distinct wings |
| `chroma.hnsw_capacity_status` (+ `_fs_type`) | when a capacity verdict may be reused |
| `chroma_sqlite.sqlite_lexical_rows` | when a scoped lexical query cannot match anything |

## Watch points
- The empty-scope rule stays in `sqlite_lexical_rows`; callers (`_closet_boosts`, union mode) must not grow copies.
- The age deadline is derived only from the public verdict and the fingerprint; if the probe ever gains another
  time-dependent predicate, the deadline rule must be revisited together with it.
- No new code may hold a read transaction across the MATCH and the decode (writer starvation, measured).

## Review history
- **Round 1** (A: conditional, B: fail, C: conditional → calibrated conditional pass). Found:
  - the capacity verdict changes with time (a 300 s grace), so a long reuse window must respect an age deadline;
  - a timestamp-digit "coarse filesystem" test misjudges exFAT, pre-6.13 Linux and network filesystems → allow-list
    by filesystem type;
  - the empty-scope check counted all collections and never fired → collection-scoped existence check, placed in the
    backend, values encoded as stored;
  - the cost model of scoped closet queries was wrong (no ranking under a `where`; cold page-in);
  - gates could not fail honestly → rewritten.
- **Round 2** (A, B, C: conditional pass). Found:
  - the age deadline needs the probe's private `has_explicit_sync_threshold` → computed inside the probe;
  - the plain `statfs` symbol returns an empty type name on Intel macOS → `statfs$INODE64`;
  - the existence walk grows with the wing's rows in other collections → bounded walk;
  - check and MATCH should share one snapshot (withdrawn in round 3);
  - "3D" rejected with measurements;
  - the deploy-time clone must be taken safely;
  - the document had stale sections → rewritten as one current design.
- **Round 3** (A, B, C: conditional pass; C1 HIGH). Found and folded in:
  - the shared read transaction starves writers in other processes (measured, above) → dropped; the check is its
    own statement;
  - the walk's exhausted-vs-limit outcome needs two statements; the skip rule is "some equality exhausted with no
    hit";
  - the private `flips_at` channel changed the probe's return shape → deadline from public fields, superset rule;
  - a 2,000 limit has a silent cliff → 20,000 (measured cost), outcome counters and an INFO line on the limit;
  - the reused dict reports the pickle age at probe time → documented as such (recomputing would break an upstream
    equality test and serves no consumer);
  - the fs-type cache keyed by `st_dev` can outlive a remount, and symbol choice by architecture breaks under
    Rosetta → no cache, symbol by presence, fresh buffer;
  - gates: negative control must fail by assertion, a cross-process writer gate, G9 clone retries, `quick_check`
    after the live transaction ends, unpickle on the copy, G10 by log lines instead of py-spy counting.
