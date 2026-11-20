# Design: search hot path — wing names, capacity verdict, closet boosts

**State:** Revision 3 (2026-11-20), after two 3-agent adversarial review rounds (history at the end). This document
states only the current design; superseded proposals are summarised in the review history, not left in the body.

## Problem (measured)
Searches on the asgard hub take 2–34 s with zero lock wait. A py-spy profile of the live hub during six real searches
(84.6 s sampled, matching the 85.6 s of the hub's `run=` times):

| Share | Code path | Measured cause |
|---|---|---|
| 34.7 % | `search_util.build_where_filter → … → chroma_known_wings → chroma_sqlite.sqlite_wing_names` | the known-wings cache lives 30 s; on a miss the first query `SELECT MIN(string_value) FROM embedding_metadata WHERE key = 'wing'` cannot use the partial index `embedding_metadata_string_value (key, string_value) WHERE string_value IS NOT NULL` → table scan, 3.3–9.3 s on the palace clone |
| 22.9 % | Chroma vector query | the actual search; out of scope |
| 22.3 % | `searcher.query._closet_boosts → ChromaCollection.lexical_search → chroma_sqlite.sqlite_lexical_rows` | one trigram FTS5 `MATCH` over the index shared by drawers and closets (≈ 1.17 M documents; 56,061 closets). Palace-wide: ranked, 0.4–2.6 s. Scoped by a `where`: no ranking, the unbounded MATCH + `_decoded_rows` + `matches_where`, 0.02–1.4 s warm (up to ≈ 1.8 s cold) — paid in full even when the scope holds no closet (24 of 31 wings, incl. `mempalace`, which 3 of the 6 profiled searches use) |
| 19.5 % | `_guards._refresh_vector_disabled_flag → hnsw_capacity_status → _hnsw_capacity_status_uncached` | the verdict cache is keyed by an `(inode, mtime_ns, size)` fingerprint but also expires after `_CAPACITY_CACHE_MAX_AGE_SECONDS = 10.0`; searches > 10 s apart re-probe (`COUNT(*)` + unpickle, ≈ 2.2–3 s) on an unchanged palace |

Goal: remove recomputation that is not needed, without changing any search output. Callers: every MCP search,
duplicate check, status path. Hidden from callers: query plans, cache policy, the skip rule.

## Fix 1 — `sqlite_wing_names` uses the partial index
The first query becomes `SELECT MIN(string_value) FROM embedding_metadata WHERE key = 'wing' AND string_value IS NOT
NULL`. `MIN()` ignores NULLs, so the result is identical by definition; plan `USING COVERING INDEX
embedding_metadata_string_value`, 0.0004–0.001 s (verified by three reviewers: identical on the clone and on a
synthetic table mixing int/float/bool/string wings; no other query in the package has this form). With misses costing
< 1 ms, `_KNOWN_WINGS_TTL` (30 s) is unchanged.

## Fix 2 — capacity verdict reuse
### Rule
A cached verdict is reused while all of these hold:
1. its fingerprint (sqlite + `-wal`, segment pickle, segment header) is unchanged — as today;
2. the **reuse window** has not elapsed (monotonic clock, as today): 3600 s when the palace's filesystem is
   allow-listed, otherwise 10 s (today's value); 10 s whenever the verdict has no HNSW element count
   (`hnsw_count is None`: it then reads files the fingerprint does not cover);
3. the **age deadline**, if the verdict has one, is in the future (wall clock).

### Age deadline (computed by the probe, stored privately)
`_hnsw_capacity_status_uncached` already knows `has_explicit_sync_threshold`. A verdict depends on time exactly when
`status == "ok"`, `not has_explicit_sync_threshold`, `divergence > 0`, and the pickle's age `< _HNSW_PERSISTENT_
DIVERGENCE_GRACE_SECONDS` (300 s): at `pickle_mtime + 300 s` it becomes "diverged" with no file changing. For that
verdict the probe returns, alongside the public dict, a private `flips_at = pickle_mtime_ns / 1e9 + 300` (the same
mtime the fingerprint holds, so no drift from the probe's own duration); the cache tuple stores it. Every other
verdict has no age deadline: an explicit-threshold palace never flips on time; an already-"diverged" verdict cannot
flip back without a file change. The public status dict is unchanged.
Clock use: the age deadline is wall clock because the probe's own predicate is (`time.time() - getmtime`); the reuse
window stays monotonic so a backward clock step cannot extend it.

### Filesystem allow-list
`_fs_type(path) -> str` reads `f_fstypename` with `statfs`:
- macOS x86_64: libc symbol `statfs$INODE64` (the plain `statfs` symbol is the legacy 32-bit-inode ABI and returns an
  empty name — measured on asgard); macOS arm64: `statfs`. Struct `statfs64`: `f_fstypename` at offset 72, size 2168.
- any other platform, or any error → `""` (not allow-listed).
Allow-list: `{"apfs"}` (measured: 0 repeated signatures in 2,000 commits; HFS+ and exFAT repeat 1,998/2,000 and stay at
10 s). Cached per `st_dev` of `chroma.sqlite3` (a mount replaced under the same path changes `st_dev`); cleared by
`reset_hnsw_capacity_cache`. Linux and other platforms keep today's 10 s — no change for them.

### Changes in code and tests (all in `backends/chroma.py`, `tests/test_hnsw_capacity.py`)
- cache tuple gains `flips_at: Optional[float]`; annotation and comment updated;
- `hnsw_capacity_status` docstring: freshness = fingerprint + reuse window + age deadline (the old sentence "rather
  than a wall-clock TTL" and "costs milliseconds" are corrected); the existing ceiling comment ("never fires in
  practice") is corrected; `_guards.py` comment on the 10 s ceiling updated;
- `test_verdict_is_not_reused_past_the_age_ceiling` is split: non-allow-listed fs re-probes after 10 s; allow-listed fs
  reuses past 10 s; `hnsw_count is None` re-probes after 10 s; implicit-threshold "ok" with divergence re-probes at
  `flips_at`; explicit threshold with divergence and an old pickle is reused past 10 s on an allow-listed fs (the
  asgard case); fingerprint change re-probes at once;
- an **unmocked** test: on macOS `_fs_type(tmp_path)` returns `"apfs"` (tmp is APFS on supported macOS).

## Fix 3 — skip the closet full-text query when the scope holds no row of the collection
### Placement and rule (in `chroma_sqlite.sqlite_lexical_rows`, for any collection)
Inside one read transaction (`BEGIN` … `COMMIT` around `read(conn)`, so the check and the MATCH see one snapshot):
1. `eqs = _string_equalities(encode_where(where))` — values encoded exactly as stored (escape prefix); `None` (any
   `$or`, `$ne`, `$nin`, mixed or numeric values, empty `$in`) or an empty list → today's path.
2. For each equality, a **bounded** existence probe: walk at most `_SCOPE_PROBE_LIMIT` (2,000) index entries of
   `embedding_metadata (key, string_value IN values)`, joined to `embeddings → segments → collections` for this
   collection, stopping at the first hit. Hit → today's path. Walk exhausted with no hit → the scope is empty → return
   `[]`. Limit reached with no hit → today's path (unknown, so no skip).
3. Any `sqlite3.Error` in the probe → today's path.
Output equality: when the skip fires, today's code returns `[]` too — every path (ranked MATCH, unranked MATCH, the
recency fallback for queries without a 3+-character token, the FTS-error fallback) ends in `_decoded_rows(where)`,
which keeps no row of an empty scope (verified by reviewers in code, and by experiment: 0 mismatches in 432 cases on
the clone and a synthetic palace). Callers (`_lexical_search_via_sqlite` ← `_closet_boosts`, union-mode drawer fetch)
cannot distinguish the paths. `_closet_boosts` is unchanged.
Cost: 0.0008–0.0068 s for the clone's closet-less wings (11–909 rows); bounded by the limit for any wing (a 600,000-row
closet-less wing would otherwise cost 0.46–0.97 s, an 837,000-row wing up to 7.7 s warm — measured).

### Rejected alternatives (with the measurements that decided)
- **COUNT instead of EXISTS:** up to 1.9 s on the largest wing.
- **`_equality_sizes`** (as in revision 1): counts every collection, never 0 for a wing with drawers.
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
# 3600 s on filesystems whose timestamps change on every write — today APFS), and — for a verdict that becomes
# 'diverged' with time alone — until that moment."

# sqlite_lexical_rows: "A filter naming a value that no row of this collection holds can match nothing; that is
# established with a bounded index walk inside the same read transaction, and the full-text query is then skipped.
# An unbounded or failed check falls back to the full query."
```

## Information hiding map
| Module | Hidden decision |
|---|---|
| `chroma_sqlite.sqlite_wing_names` | index-only enumeration of distinct wings |
| `chroma.hnsw_capacity_status` (+ `_fs_type`) | when a capacity verdict may be reused |
| `chroma_sqlite.sqlite_lexical_rows` | when a scoped lexical query cannot match anything |

## Watch points
- The empty-scope rule stays in `sqlite_lexical_rows`; callers (`_closet_boosts`, union mode) must not grow copies.
- The age deadline is produced by the probe that evaluates the age predicate; it must not be re-derived elsewhere.

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
  - the age deadline needs the probe's private `has_explicit_sync_threshold` → computed inside the probe from the
    pickle mtime, stored in the cache tuple (the earlier signature would have re-probed on every asgard search);
  - the plain `statfs` symbol returns an empty type name on Intel macOS → `statfs$INODE64`;
  - the existence walk grows with the wing's rows in other collections → bounded walk;
  - check and MATCH should share one snapshot;
  - "3D" rejected with measurements;
  - the deploy-time clone must be taken safely (read transaction in a separate process, segment files in the same
    window, pickle-last check, read-only frozen copy, a working copy per code version);
  - the document had stale sections → rewritten as one current design.
