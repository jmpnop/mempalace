# Design: scope the "already mined" prefetch to the files being mined

**State:** Revision 1 (2026-11-21), draft. Code read, nothing measured yet: the measurements M1–M4 below run on the
palace clone after the search hot-path timing gates finish (they would disturb those timings), then three-agent
adversarial review, then gates and code.

## What we're building (Phase 1, extracted from the code)
Every project mine (CLI `mempalace mine`, `mempalace init`, the hub's `mempalace_mine` tool, the service, and every hook
— hooks spawn the CLI) calls `palace.mined.prefetch_complete_mtimes(collection, files)` once (`miner.py:2236`) to learn
which candidate files are already mined at their current mtime, so they can be skipped without a per-file
`collection.get` (~0.1 s each). On the Chroma backend it reads **every drawer's** metadata in the collection (4 keys +
the escape flag, ≈ 1.17 M drawers × up to 5 rows on asgard) and discards in Python every row whose `source_file` is not
a candidate (`mined.py:435`): ≈ 30 s per mine (13.5 % of a mine, doc 07). The narrowing to candidate files exists only
for the `sqlite_exact` backend (`mined.py:409-422`, ≤ 5,000 string paths). While the pass runs,
`sqlite_iter_metadata` holds a read transaction and `palace_db_lock` (`chroma_sqlite.py:1082-1110`): on a rollback
palace that blocks writers in other processes for the whole pass, and in the hub it blocks in-process readers.

- Callers: the project miner only (`miner.py:2236`); the convo miner uses the sibling `prefetch_mined_set`, already
  scoped (≤ 50 files).
- Input: the collection and the candidate files (absolute resolved paths; stored `source_file` uses the same form,
  `miner.py:1781`). Output unchanged: `{source_file: tuple(mtimes of complete groups)}` or `None`.
- Hidden from callers: how the read is scoped, chunked and bounded; the backend.
- Edge cases from the code: escaped stored values (`"\x01mpesc:"` prefix, lone surrogates; `stored_text.py:41-80`);
  a source with an incomparable value anywhere is "unprovable" and must stay out of the result (`mined.py:437-440`);
  SQLite's variable limit; very large candidate lists; `MetadataRecoveryPending` and stream errors → `None`.
- Rejected scopes (from the code): by wing — an "unprovable" row filed under another wing would no longer be seen,
  so the prefetch could prove a file complete that the per-file check would re-mine (a behaviour change); by path
  prefix (`LIKE`) — same hazard for other spellings, and escaped values never match a prefix.

Output equality argument: today every row whose `source_file` is not in `wanted` is discarded before it affects any
state (`mined.py:435`, the first statement of the loop body), so reading only rows whose `source_file` is in `wanted`
yields the same `groups`, `unprovable` and result by construction — provided the scope compares **stored (encoded)**
values and every encoded form of a wanted path is included.

## Design A — "Scoped stream": the metadata stream accepts a value set per key
- `chroma_sqlite._sqlite_iter_metadata(..., equals)`: `equals` values may be a scalar (today) or a collection of
  strings; a collection becomes `r.string_value IN (…) AND r.string_value IS NOT NULL` over `encode_filter(v)` of each
  value. Hidden: encoding, chunking (≤ `_SCOPE_CHUNK` values per statement, merged in row order), the index plan.
- `mined.prefetch_complete_mtimes`: passes `{"source_file": wanted}` on every backend whose stream supports it (Chroma
  and `sqlite_exact`), for 1 ≤ |wanted| ≤ `_SCOPE_MAX` (measured, M2); above that, today's full pass.
```python
def _sqlite_iter_metadata(conn, collection_name, keys, require_key, equals: Optional[dict] = None): ...
#   equals: {key: str | Collection[str]} — drawers whose stored string under each key is (one of) the value(s)
def prefetch_complete_mtimes(collection, source_files) -> Optional[dict[str, tuple]]: ...   # unchanged
```
Data flow: miner → prefetch (scoped) → `_fast_collection_metadata(equals=…)` → `ChromaCollection.iter_metadata` →
`sqlite_iter_metadata` → one read transaction, chunked `IN` statements → decoded rows → the unchanged loop.
Depth: interface simplicity 2 · power 2 · hiding 2 (encoding, chunking, plan) · error absorption 1 (stream errors still
→ `None`) · generality 2 (any caller of the stream gains set scopes) = **9**.

## Design B — "Summary in SQL": a dedicated per-source summary reader
- New `chroma_sqlite.sqlite_mined_summary(palace, collection, source_files)` computing, per `(source_file,
  source_mtime)`, the drawer count, the largest `chunk_total`, whether any drawer lacks one, and whether any value is
  incomparable, with `GROUP BY` over the scoped rows; `prefetch_complete_mtimes` turns the summary into the result.
Depth: simplicity 2 · power 2 · hiding 1 (the completeness rule is split between SQL and `mined.py`) · error
absorption 1 · generality 0 (one caller) = **6**.
Assessment: fewer Python rows, but the completeness rule — which `file_already_mined` and the parity test
(`tests/test_miner.py:4000`) pin — would exist twice, in SQL and in Python (type coercion, `_is_comparable`,
`_is_plain_number`, `normalize_version` defaults), and every future change to the rule would have to be made in both.

## Evaluation (Phase 5, condensed) and recommendation
A is stronger on information hiding (one rule, one place), consistency (extends an existing parameter that already
scopes `sqlite_exact` and wing passes), obviousness and change amplification; B is stronger only on raw row count,
which scoping already reduces from ≈ 1.17 M drawers to the candidates' drawers. No temporal decomposition in either.
**Recommend A.** Trade-off accepted: Python still decodes every scoped row (a few thousand per typical mine).
Red flags checked: no pass-through (the stream parameter gains behaviour), no special-casing by backend beyond "stream
supports set scopes", no configuration knob exposed (`_SCOPE_CHUNK`/`_SCOPE_MAX` are module constants set from M2).
Remaining risks: the chunk/limit constants (M2); the read transaction still spans all chunks (M3 measures its length);
values that cannot be encoded (`encode_filter` raises → today's full pass for that mine).

## Interface comment (Phase 7)
```python
# _sqlite_iter_metadata, ``equals`` (amended):
#   ``equals`` limits the scan to drawers whose stored string under each key equals the given value, or one of the
#   given values when a collection of strings is passed, through the ``(key, string_value)`` index; values are
#   compared in their stored (escaped) form. A set larger than one statement can carry is read in several
#   statements inside the same read transaction, still in row order.
#
# prefetch_complete_mtimes (added sentence):
#   The read covers only drawers whose ``source_file`` is one of ``source_files`` (up to _SCOPE_MAX of them), which
#   are the only rows that can affect the result.
```

## Measurements before review (M1–M4, on the APFS clone, after the search timing gates)
- M1 Today's cost split on the clone for a real 20-file and a 1,400-file candidate list: SQL time vs Python time.
- M2 Scoped cost vs |wanted| (1, 20, 200, 1,400, 5,000, 20,000) and chunk size (500, 900, 2,000): find `_SCOPE_MAX`
  where the scoped read stops beating the full pass, and the chunk size with the best plan.
- M3 Read-transaction and `palace_db_lock` hold time, today vs scoped.
- M4 Output equality on the clone: scoped result == today's for the asgard project trees, for synthetic escaped paths,
  for an unprovable row in another wing, and for a source whose drawers span several mtimes.

## Information hiding map
| Module | Hidden decision |
|---|---|
| `chroma_sqlite._sqlite_iter_metadata` | how a set scope becomes indexed, chunked, encoded SQL |
| `mined.prefetch_complete_mtimes` | which rows can affect the skip decision, and when scoping is worth it |

## Watch points
- The completeness rule stays in `mined.py` only; the stream stays rule-free.
- Scoping must stay by `source_file` value; never by wing or path prefix (see rejected scopes).
