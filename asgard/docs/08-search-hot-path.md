# 08 — Search hot path: wing names, capacity verdict reuse, empty-scope closet query

**Status:** implemented (`877cd3e5`, tests `5ded2488`), deployed on asgard 2026-11-21 from `711701dc`
(byte-identical install, patched chromadb bindings `2fedfa99…` reinstalled). Gates G1–G7, G12, G8, G9, G10 met;
G10p (live py-spy profile) is recorded below when taken.
**Design:** [`../designs/search-hot-path.DESIGN.md`](../designs/search-hot-path.DESIGN.md) (revision 4, three
adversarial review rounds). **Gates:** [`../gates/search-hot-path/`](../gates/search-hot-path/) (`GATES.md` before
the deploy, `GATES-deploy.md` after it, in run order).

## Problem (from 05, issue 2)
Searches took 2–34 s on an idle hub with zero lock wait. A py-spy profile of six real searches put 76.5 % of the time
outside the vector query itself, in three places that recompute what has not changed:

| Share | Path | Cause |
|---|---|---|
| 34.7 % | `sqlite_wing_names` | the first query cannot use the partial index → full scan of `embedding_metadata`, 3.3–9.3 s |
| 22.3 % | `_closet_boosts → sqlite_lexical_rows` | an unbounded FTS5 MATCH even when the scope holds no closet (24 of 31 wings) |
| 19.5 % | `hnsw_capacity_status` | a verdict older than 10 s is re-probed (`COUNT(*)` + unpickle, 2–3 s) on an unchanged palace |

## Changes
1. **`sqlite_wing_names`** names `string_value IS NOT NULL` in every query, so the partial index
   `embedding_metadata_string_value` serves it. `MIN()` ignores NULLs, so the result is identical by definition.
2. **Capacity verdict reuse:** an unchanged verdict is reused for 3600 s when the palace is on APFS (`_fs_type`
   reads `f_fstypename` through `statfs$INODE64` when libc has it), 10 s elsewhere or when `hnsw_count` is
   unknown; an "ok" verdict with a divergence and a pickle younger than 300 s is re-probed when the grace ends. Every
   uncached probe logs `hnsw capacity probe: <collection> reason=<first-call|uncached|fingerprint|window|deadline>`.
3. **Empty-scope skip:** `sqlite_lexical_rows` first walks at most 20,000 index entries per string equality of the
   `where`, in its own statement (no transaction spans the walk and the MATCH); if some equality's walk is exhausted
   with no row of the collection, it returns `[]` without the MATCH. A walk at the limit or a walk error falls back to
   the unchanged path. Outcome counters and one INFO line per scope that reaches the limit.

## Results (gate runs on an APFS clone of the live palace unless marked live)
| Gate | Measured |
|---|---|
| G1 wing names | 31 names, identical lists; median 3.27 s → 0.0022 s; plan `USING COVERING INDEX embedding_metadata_string_value` |
| G2 / G2r | 101 unit tests pass; real `_fs_type`: apfs (clone), apfs (pytest tmp), "" (missing path) |
| G3 negative control | the 14 new-behaviour tests fail on BASE, each by `AssertionError`; the 87 preserved tests pass on both |
| G4 lexical + boosts | 0 mismatches in 205 clone + 9 synthetic cases and 8 × 33 timed cases (ABBA order); closet-less wings 1.01–1.06 s → 1.7–4.3 ms; closet wings and palace-wide within 1.05 × BASE + 10 ms; walk on a hit 0.065–0.232 ms; walk at the limit 36.6 ms |
| G5 `tool_search` | full JSON identical for 38 clone + 5 synthetic cases; no vector-disabled run |
| G12 writer | separate-process writer, 3 alternating 120 s runs per side: 0 errors on both; median of runs: p99 commit 100.5 ms → 93.8 ms; max commit 2.29 s → 2.48 s (limit 2.57 s) |
| G6 suite | upstream suite green apart from the two failures documented in 06; ruff clean |
| G8 deploy | installed `mempalace/` equals the fork tree; bindings `2fedfa99…`; `/healthz` 200 (14 s after bootstrap) |
| G9 frozen + live | clone of the live palace in 1 attempt (read transaction held 0.063 s); `quick_check` ok; both capacity verdicts "ok"; BASE = FORK for the six profiled queries; live hub results equal to the frozen FORK results (strict check: no row inserted since the clone) |
| G10 live probes | 6 searches ≥ 40 s apart: 3 probe lines, all `reason=fingerprint`, over 4 recorded fingerprint changes (allowed 5); the hub's first probe at start-up logged `reason=first-call` |

The six profiled queries through the live hub (client-side seconds, `mempalace-mcp` proxy), idle hub:

| Query | before (ef89c2cb, 2026-11-20) | after (G9, 2026-11-21) |
|---|---|---|
| six/0 (wing-scoped) | 238.47 | 6.70 |
| six/1 (wing-scoped) | 17.44 | 2.57 |
| six/2 (palace-wide) | 8.65 | 5.49 |
| six/3 (palace-wide) | 2.04 | 3.85 |
| six/4 (wing-scoped) | 3.47 | 2.55 |
| six/5 (palace-wide) | 4.63 | 1.95 |

The "before" column was recorded on 2026-11-20 without a check for concurrent hub work, so its 238 s first entry is
most likely lock wait rather than search cost; the per-path numbers above (G1, G4) are the controlled comparison.

## What the gates also showed
- **A mine through the hub still stalls searches.** During G10 another session ran `mempalace_mine` through the hub
  (702 s). Searches in that window: one waited 311 s for the hub lock (the mine's write phase), two ran 114 s and
  322 s with no lock wait (the post-mine check tail of 06). This is the open `palace_db_lock` issue of 06, not a
  regression; with no mine running the same queries took 1.9–6.7 s (G9).
- **Gate harness bug, fixed before G5 counted:** importing `mempalace.mcp_server` points fd 1 at stderr (MCP stdio
  protection), so the harness's JSON went to stderr and G5's first run compared empty files (it failed, as it should).
  The harness now writes to a duplicate of the real stdout taken before any import (`711701dc`).
- **Evidence ledgers are private:** gate-check's automatic evidence carries machine paths, so the checked copies of
  both ledgers live outside the repository; the boxes in this repository stay unchecked.

## Rollback
`uv tool install <fork at ef89c2cb> --python 3.13 --reinstall`, then re-install the patched chromadb wheel and check
the bindings hash (01). The three changes are independent; each can be reverted alone.
