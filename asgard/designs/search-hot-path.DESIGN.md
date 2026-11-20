# Design: search hot path — wing names, capacity verdict, closet boosts

## Decision Date
2026-11-20

## What We're Building
Searches on the asgard hub take 2–34 s with zero lock wait (hub `CALL` lines; doc 05 issue 2). A py-spy profile of the
live hub during six real searches (84.6 s sampled, matching the 85.6 s of `run=` times) attributes the time to:

| Share | Code path | Measured cause |
|---|---|---|
| 34.7 % | `search_util.build_where_filter → palace_wing_aliases → known_wings → chroma_known_wings → sqlite_wing_names` | the cache (`_KNOWN_WINGS_TTL = 30 s`) expires between searches; the rebuild's first query `SELECT MIN(string_value) FROM embedding_metadata WHERE key = 'wing'` cannot use the partial index `embedding_metadata_string_value (key, string_value) WHERE string_value IS NOT NULL`: plan `SEARCH embedding_metadata` (table), **9.315 s** on the palace clone |
| 22.9 % | Chroma vector query (`RustBindingsAPI._query`) | native HNSW search — the actual search; not in scope |
| 22.3 % | `searcher.query._closet_boosts → ChromaCollection.lexical_search → chroma_sqlite.sqlite_lexical_rows` | one trigram FTS5 `MATCH` over the index shared by drawers and closets (≈ 1.17 M documents; closets are 56,061), `ORDER BY rank` scores every palace-wide match: 1.3–2.6 s palace-wide, **3.9–7.2 s** for a wing filter (no `LIMIT` under a `where`) |
| 19.5 % | `_guards._refresh_vector_disabled_flag → hnsw_capacity_status → _hnsw_capacity_status_uncached` | the verdict cache has a signature check *and* `_CAPACITY_CACHE_MAX_AGE_SECONDS = 10.0`; searches > 10 s apart re-probe (pickle load + `COUNT(*)`, ≈ 3 s) although `chroma.sqlite3` was unchanged for 14 min |

Goal: remove the recomputation that is not needed, without changing any search result. Callers: every MCP search
(`tool_search`), duplicate checks, status. Hidden from callers: caching policy, query plans, the skip rule.

## Measured facts used below (palace clone, Python sqlite 3.50.4)
- Fix 1: adding `AND string_value IS NOT NULL` to the first query → plan `USING COVERING INDEX
  embedding_metadata_string_value`, **0.000 s**, same 31 wings. `MIN()` ignores NULLs, so the result is identical by
  definition.
- Closets per wing (four largest code wings): 45,093; 5,860; 4,494; 536; **the `mempalace` wing: 0** (its drawers were
  filed directly; three of the six profiled searches are scoped to it).
- Restricting FTS5 to a wing's closet rowids (`MATCH ? AND rowid IN (…)`) returns identical rows and rank values but
  costs 27–1,382 s (FTS5 re-evaluates the match per chunk) — rejected.
- An in-memory whole-word index of all closets: 56,061 docs, 33.4 MB, load 17.6 s + tokenize 1.2 s, query 0.6–6 ms —
  but whole-word matching ≠ trigram matching, so boosts (and thereby results) change.
- APFS stores nanosecond timestamps; `chroma.sqlite3` mtime unchanged across the profile (1795210841).

## Fix 1 — the wing-name query (ponytail rung 6: one well-named function, no new abstraction)
`sqlite_wing_names`: first query becomes `… WHERE key = 'wing' AND string_value IS NOT NULL`. Same result set (MIN
ignores NULL); the docstring's promise ("one index seek per wing") becomes true. With a miss now costing < 1 ms, the
30 s `_KNOWN_WINGS_TTL` stays as it is.

## Fix 2 — capacity verdict freshness
### Design 2A — "Long fixed backstop"
Raise `_CAPACITY_CACHE_MAX_AGE_SECONDS` from 10 s to e.g. 3600 s. Interface unchanged.
Depth: simplicity 2 · power 1 · hiding 1 · error absorption 1 · generality 1 = **6**.
Assessment: on a filesystem with coarse (1–2 s) timestamps, two writes within one timestamp tick with an unchanged
size would leave a stale verdict for an hour — the case the backstop exists for (docstring: "only as a backstop for
filesystems with coarse timestamps").
### Design 2B — "Resolution-aware backstop"
`hnsw_capacity_status` keeps the `(inode, mtime_ns, size)` signature as the freshness rule and applies the short
backstop only when the signature cannot tell writes apart: when the fingerprinted files' `st_mtime_ns` show no
sub-second component (all `mtime_ns % 1_000_000_000 == 0`, i.e. a 1-s-or-coarser filesystem), the 10 s backstop
applies as today; otherwise a long backstop (3600 s) bounds the cache only against pathological clock behaviour.
Interface unchanged; the rule lives in one helper `_capacity_backstop_seconds(fingerprint)`.
Depth: simplicity 2 · power 2 · hiding 2 · error absorption 2 · generality 2 = **10**.
Assessment: keeps today's behaviour exactly where the signature is weak, removes the re-probe where it is strong.
Edge: a fine-grained filesystem whose file happens to have mtime on an exact second → treated as coarse (safe side).
### Choice: 2B.

## Fix 3 — closet boosts
### Design 3A — "Empty-scope skip" (results identical by construction)
`_closet_boosts` (or `sqlite_lexical_rows` for the closets collection) first resolves the `where` filter's wing/room
equalities to a closet count through the `(key, string_value)` partial index (the same `_equality_sizes` primitive the
drawer path already uses); a count of 0 → return `{}` without the FTS query (the FTS query could only have returned
rows that the filter then drops). Any other count → today's query unchanged. Palace-wide searches unchanged.
Depth: simplicity 2 · power 1 · hiding 2 · error absorption 2 · generality 1 = **8**.
### Design 3B — "In-memory closet index"
A per-palace whole-word inverted index of closet documents, rebuilt when the database stamp changes; boosts computed
in memory (0.6–6 ms). Changes which closets rank first (whole words vs trigrams), costs ≈ 19 s per rebuild after any
closet write (every mine), 33 MB per palace.
Depth: simplicity 2 · power 2 · hiding 2 · error absorption 1 · generality 1 = **8**.
### Design 3C — "Exact BM25 over the scoped closets"
Recompute FTS5's `bm25()` for the wing's closets from the FTS5 shadow tables (trigram document frequencies, doc sizes,
averages) to keep ranks identical without a palace-wide match. Exact only while it mirrors FTS5's formula and
tokenizer; a SQLite upgrade can silently change it. Not pursued.
### Choice: 3A now; 3B recorded as a separate, user-owned decision
3A is the only option measured to keep every result identical while removing the cost for scoped searches over wings
without closets (3 of the 6 profiled searches). It does nothing for scoped searches over wings with closets (3.9–7.2 s)
or palace-wide ones (1.3–2.6 s); those need 3B or 3C, both of which change or risk changing results — that trade is
the operator's, with a relevance study, and is out of this design.

## 18-principle evaluation (chosen: Fix 1 + 2B + 3A, vs. alternative bundle 2A + 3B)
| # | Principle | Chosen | Rating | Alternative | Rating |
|---|---|---|---|---|---|
| 1 | Complexity minimization | three local changes, no new module | STRONG | new index + cache policy | NEUTRAL |
| 2 | Symptom awareness | no new state to keep consistent | STRONG | index invalidation is a new unknown | WEAK |
| 3 | Cause elimination | fixes the plan bug at its source | STRONG | caches around it | NEUTRAL |
| 4 | Deep modules | interfaces unchanged | STRONG | interfaces unchanged | STRONG |
| 5 | Information hiding | backstop rule hidden in one helper | STRONG | index hidden | STRONG |
| 6 | General-purpose | 2B general to any fs | STRONG | 3B closets only | NEUTRAL |
| 7 | Different layer, different abstraction | yes | STRONG | yes | STRONG |
| 8 | Pull complexity down | yes | STRONG | yes | STRONG |
| 9 | Composition | reuses `_equality_sizes` | STRONG | parallel matcher | WEAK |
| 10 | Define errors out | empty scope defined as empty | STRONG | stale index possible | WEAK |
| 11 | Design it twice | — | N/A | — | N/A |
| 12 | Comments non-obvious | two short WHY comments | STRONG | needs semantics note | NEUTRAL |
| 13 | Comments-first | trivial contracts | STRONG | heavier | NEUTRAL |
| 14 | Naming | `_capacity_backstop_seconds` precise | STRONG | — | NEUTRAL |
| 15 | Consistency | mirrors drawer-path primitives | STRONG | new pattern | WEAK |
| 16 | Obviousness | yes | STRONG | moderate | NEUTRAL |
| 17 | Strategic | preserves results; defers the ranking trade explicitly | STRONG | changes ranking silently | WEAK |
| 18 | No temporal decomposition | by what each hides | STRONG | by what each hides | STRONG |
Chosen: STRONG 17, NEUTRAL 0, WEAK 0. Alternative: STRONG 6, NEUTRAL 6, WEAK 5.

## Interface comments
```python
def sqlite_wing_names(palace_path: str, limit: int = 10_000) -> Optional[list[str]]:
    """Every distinct string ``wing`` in ``chroma.sqlite3``, or ``None``.
    ... (unchanged) ...
    Every query names ``string_value IS NOT NULL`` so SQLite can use the partial ``(key, string_value)`` index;
    without it the first query scans the whole metadata table (9 s on a 1.2 M-drawer palace)."""

def _capacity_backstop_seconds(fingerprint: tuple) -> float:
    """How long a capacity verdict with this fingerprint may be reused when its files look unchanged.

    The ``(inode, mtime_ns, size)`` signature is the freshness rule. It can miss a rewrite only on a filesystem
    whose timestamps are whole seconds (two writes in one second, same size); there the short backstop applies.
    On filesystems with sub-second timestamps the long backstop only bounds pathological cases.
    """

# _closet_boosts: when ``where`` restricts to metadata values that no closet holds, there is nothing the full-text
# query could return that the filter would keep, so it is not run. Scoped searches over wings filed without closets
# otherwise paid a palace-wide ranking (seconds) to produce an empty boost.
```

## Information Hiding Map
| Module | Hidden decision |
|---|---|
| `chroma_sqlite.sqlite_wing_names` | how the distinct wings are enumerated (index-only seeks) |
| `chroma._capacity_backstop_seconds` | when a stat signature is trusted |
| `searcher.query._closet_boosts` | when the closet full-text query can be skipped |

## Temporal Decomposition Watch Points
- Do not move the empty-scope check into callers of `_closet_boosts`.
- Do not split the backstop decision from the fingerprint it is computed from.

## Remaining risks (for the review)
1. `where` shapes: `$and`/`$or`/`$in`/`$ne` — the skip may apply only when a conjunction of string equalities has a
   0-count member; anything else → today's path.
2. Closets whose metadata value is numeric (`int_value`) — the count must use the same value encoding as the FTS
   path's filter, or the skip could drop a real hit.
3. The capacity verdict guards against a diverged HNSW (segfault class): the long backstop must never apply when the
   fingerprint is incomplete (missing segment id, `None` sqlite count) — today those are never cached.
4. Gate G4 (closet boosts identical) and G5 (search results identical) must include wings with and without closets,
   numeric-valued filters, `$and` with room, and palace-wide queries.

---

# Revision 2 — 2026-11-20: review round 1 → CONDITIONAL PASS (A: CONDITIONAL, B: FAIL, C: CONDITIONAL)

B's FAIL is calibrated to CONDITIONAL PASS: each of its HIGH findings has a contained fix inside the same three
changes; none requires a different primary abstraction. Fix 1 was confirmed exact by all three (A: identical first
value, 3.44 s → 0.0004 s; C: identical on the clone and on a synthetic table with int/float/bool/string wings; A: no
other query in the package has the same missing-partial-index form).

| # | Finding (raised by) | Required change |
|---|---|---|
| R1 | The capacity verdict depends on wall-clock time, not only on files: `stale_below_threshold` flips "ok" → "diverged" when the pickle reaches 300 s of age (`_HNSW_PERSISTENT_DIVERGENCE_GRACE_SECONDS`), for collections without explicit `hnsw:sync_threshold` and 0 < divergence ≤ threshold; 2B's 3600 s backstop would hold the stale "ok" up to an hour (B2, A3, C1 — measured by C) | a cached verdict carries an **expiry**: if it depends on age (`not has_explicit_sync_threshold and divergence > 0 and hnsw_metadata_age_seconds is not None`), expiry = the wall-clock moment the age reaches the grace (re-probe then); expiry uses `time.time()` (a monotonic clock stops during sleep, C1) |
| R2 | `mtime_ns % 1e9 == 0` misclassifies exFAT (10 ms; mtime frozen while the writer keeps the file open — measured), pre-6.13 Linux ext4/xfs/btrfs (jiffy ticks with ns digits), NFS/SMB (B3, A4, C2) | the long backstop applies only on an **allow-list of filesystem types** read once per palace with `statfs` (macOS `f_fstypename == "apfs"`); every other filesystem keeps today's 10 s; absent files never decide (A8) |
| R3 | `index_metadata.pickle` absent → the verdict reads the payload size, which is not fingerprinted (C7) | when `hnsw_count is None` the short backstop applies |
| R4 | `_equality_sizes` counts every collection, so it can never return 0 for a wing with drawers: Fix 3A as written never fires (B1, A1, C5 — measured: wing `mempalace` 11 rows, 0 closets) | an **`EXISTS … LIMIT 1` restricted to the target collection** (join embeddings → segments → collections), short-circuiting on the first row: 0.000 s for wings with closets, ≤ 0.002 s for wings without (measured A, C); a COUNT would add up to 1.9 s (rejected) |
| R5 | Fix 3 placement: the searcher must not know Chroma sqlite (B7, A6) | the skip lives in **`chroma_sqlite.sqlite_lexical_rows`**, for any collection: `_string_equalities(encode_where(where))` must return equalities (else today's path); if any equality has no row in that collection → return `[]` (exactly today's output for that input — `_decoded_rows(where)` keeps nothing). `_closet_boosts` unchanged. Values are encoded as stored (escape prefix); numeric values never reach the skip (`_string_equalities` returns None) |
| R6 | Cost model misstated: under a `where` there is no `ORDER BY rank`; warm cost is the unbounded MATCH + `_decoded_rows` (≤ 1.4 s); the 3.9–7.2 s was cold page-in; pushing the wing filter into SQL is slower (2.4–7.4 s) (A7, B8) | table corrected here; the push-down is recorded as rejected |
| R7 | An in-house option was missing: `_filtered_candidate_rows` reads a small scope's own text (≤ 20,000 rows) with the trigram-equivalent substring test (B8) | **Design 3D** — evaluated, adopted **only if** the exact-equality gates (G4/G5 below) pass on the clone and the synthetic palace; otherwise recorded as rejected with the measured difference |
| R8 | Gates could not fail honestly: G2 tested a known-wings change the design declines; G4 had an "explained differences" escape and a speed claim 3A cannot meet; G5 compared ids only; G9 compared against a palace other sessions keep writing to and passed on error responses (B4, B5, B6, A5, C3, C4, C6) | gates rewritten (GATES.md, revision 2): exact equality of the full `_closet_boosts` dict and of the full `tool_search` JSON minus timings; required query classes; a synthetic palace with int/float/escaped wings, `$and` with room, `$in` aliases, an AAAK-refetch case, a no-token query; speed claimed only for scopes without closets, no regression > 5 % elsewhere; G9 runs installed vs fork code on one fresh APFS clone of the live palace (frozen state) plus a live smoke check that fails on any `error` or empty result |
| R9 | Tests and comments encode the old policy: `tests/test_hnsw_capacity.py::test_verdict_is_not_reused_past_the_age_ceiling`, `_guards.py` comment on the 10 s ceiling, `chroma.py` comments ("never fires in practice"; "costs milliseconds") (B9, A8) | listed as changes: the test splits into coarse-fs (10 s re-probe), allow-listed fs (reuse past 10 s), age-dependent expiry, payload-absent cases; comments corrected |

## Fix 2 (final) — `_capacity_cache_expiry(fingerprint_paths, status, probed_at_wall)`
```python
def _capacity_reuse_deadline(palace_path: str, status: dict, probed_at: float) -> float:
    """Wall-clock time until which a capacity verdict may be reused while its files' signatures are unchanged.

    The stat signature catches every write only where timestamps are fine-grained and updated per write; that is
    established per palace from the filesystem type (an allow-list, today APFS), never inferred from timestamp
    digits. Elsewhere the reuse window stays at the short backstop (10 s), as before this change.
    A verdict can also change with no file changing: a sub-threshold divergence becomes "diverged" once the HNSW
    metadata is older than the grace period. Such a verdict is reused at most until that moment.
    A verdict measured without an HNSW element count reads files the signature does not cover: short backstop.
    """
```
## Fix 3 (final) — in `sqlite_lexical_rows`
```python
# A filter whose string equalities name a value no row of this collection holds can match nothing, and the
# full-text query below would only produce rows that `_decoded_rows(where)` then drops. Checking one EXISTS per
# equality (stops at the first row; collection-scoped, values encoded as stored) replaces a palace-wide MATCH that
# costs seconds — scoped searches over wings filed without closets paid it to produce an empty boost.
# Consistency: the check and the MATCH read separate snapshots; a mine that commits a wing's first closet between
# them yields the result the request would have had a moment earlier — acceptable for a rank boost.
```
Design 3D (filter-driven read of small scopes) is evaluated against the same gates before it can be adopted.
