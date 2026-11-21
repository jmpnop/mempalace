# Gates: search hot path (wing names, capacity verdict reuse, empty-scope lexical skip)

OWNS: mempalace/backends/chroma_sqlite.py, mempalace/backends/chroma.py, mempalace/mcp_server/_guards.py, tests/**, asgard/**

Scope: on the live asgard hub a search no longer runs the unindexed wing-name scan, re-probes HNSW capacity while the
verdict's files, reuse window and age deadline allow reuse, or runs a full-text query for a scope that holds no row of
the collection; every search returns exactly the same output as before, and no SQLite lock is held longer than
before. (Gates revision 4, matching design revision 4.)
The after-deploy gates G8, G10, G9, G10p and G11 are in GATES-deploy.md, in the order they must run (G10 needs
the restarted hub's first capacity probes), so that no after-deploy gate can be recorded before the deploy.

Conventions for every CHECK (enforced inside the scripts, each of which prints its own diagnosis):
- BASE = a `git worktree` of the deployed commit ef89c2cb; FORK = the branch head. Both run with the same interpreter
  and chromadb build (the fork's `.venv`), swapping only the `mempalace` source via `PYTHONPATH`; each run asserts
  `mempalace.__file__` lies in the tree it means to test.
- Every comparison runs each side on its own fresh writable APFS working copy (`cp -cR`) of a frozen, read-only clone;
  the frozen clone's fingerprint (stat of every file) is checked unchanged afterwards. Config points at the copy.
- Timings: median of >= 5 interleaved warm runs per case (one discarded warm-up), never single runs. Output equality
  is deterministic and is checked on one run per case; cases slower than 20 s per run (scoped union-mode drawer
  reads, 26–61 s) are equality-only.

Evidence: gate-check records the evidence (it carries machine paths) in a private copy of this ledger and of
GATES-deploy.md kept outside the repository; the boxes here stay unchecked.

- [ ] G1: wing names — on the palace clone FORK's `sqlite_wing_names` equals BASE's list, its first query's plan uses
  `embedding_metadata_string_value`, and the whole call's median time is < 0.05 s; a synthetic palace with int,
  float, bool and string wings gives equal lists too
  CHECK: bash asgard/gates/search-hot-path/g1_wing_names.sh "$PROFILE_PALACE"
  EXPECT: G1_WING_NAMES_OK
  EVIDENCE: pending

- [ ] G2: capacity reuse rule and scope walk (unit tests, simulated clocks, mocked filesystem type), all passing on
  FORK: non-allow-listed fs re-probes after 10 s; allow-listed fs reuses past 10 s with unchanged files;
  `hnsw_count is None` re-probes after 10 s; "ok" with divergence and a pickle younger than 300 s is reused before
  pickle mtime + 300 s and re-probed after it; an explicit-threshold verdict with divergence and a pickle older than
  300 s is reused past 10 s on an allow-listed fs; an already-diverged verdict is reused within its window; any
  fingerprint change re-probes at once; a backward wall-clock step does not extend the reuse window; a reused verdict
  equals the probe's dict; each uncached probe logs one reason line; `_fs_type` selects
  `statfs$INODE64` when present and `statfs` otherwise; empty-scope skip returns `[]` without running a MATCH; a walk
  at the limit falls back and counts `limit`; a walk error falls back and counts `error`; an `$and` with one exhausted
  equality skips; an `$in` skips only when the walk over all its values is exhausted with no hit
  CHECK: .venv/bin/python -m pytest -q tests/test_hnsw_capacity.py tests/test_chroma_sqlite_scope_walk.py
  EXPECT: /\d+ passed/
  EVIDENCE: pending

- [ ] G2r: the real (unmocked) `_fs_type` returns "apfs" for the palace clone's directory and for pytest's tmp dir on
  this Intel Mac (the `statfs$INODE64` path), "hfs" for an HFS+ volume when one is mounted (skipped with a printed
  note otherwise), and "" for a path that does not exist
  CHECK: .venv/bin/python -I asgard/gates/search-hot-path/g2r_fs_type.py "$PROFILE_PALACE"
  EXPECT: G2R_FS_TYPE_OK
  EVIDENCE: pending

- [ ] G3: negative control — the new-behaviour tests fail on BASE, each by `AssertionError` (tests reach new names
  through `getattr(..., default)` and `monkeypatch.setattr(..., raising=False)`, so no failure is an import or
  attribute error), and pass on FORK. New behaviour (14): in `TestCapacityReuseWindow` — allow-listed reuse past
  10 s, explicit-threshold old-pickle reuse, young-pickle reuse before the grace ends, re-probe the moment the grace
  ends (BASE reuses for up to 10 s after it), already-diverged reuse, reason log line, `_fs_type` symbol choice,
  `_fs_type` of a missing path; in `test_chroma_sqlite_scope_walk.py` — empty-scope skip runs no MATCH, `$and` with
  one exhausted equality skips, limit outcome counted, limit logged once, error outcome counted, wing-name first
  query uses the index. Behaviour preserved (pass on both): every other test in both files, incl. the 10 s re-probe
  off the allow-list, `hnsw_count is None` 10 s, backward wall step, fingerprint change, limit and error fallback
  output, `$in` partial, unevaluable filters, the recency path, wing names without the index. The script prints
  both lists and the exception type of each BASE failure, and fails if any BASE failure is not an `AssertionError`
  CHECK: bash asgard/gates/search-hot-path/g3_negative_control.sh
  EXPECT: G3_NEGATIVE_CONTROL_OK
  EVIDENCE: pending

- [ ] G4: lexical rows and closet boosts identical — for these classes: wing without closets (small and a synthetic
  600,000-row one past the walk limit), wing with closets of each size band, palace-wide, `$and` wing+room, `$in`
  aliases, an AAAK-refetch case, a query with no 3+-character token, a non-ASCII query (Greek final sigma), escaped
  wing values, numeric wing values, union-mode drawer reads (equality only) — FORK's `sqlite_lexical_rows` output and
  whole `_closet_boosts` dict (rank, distance, preview per source) equal BASE's on the palace clone and the synthetic
  palace; median boost time is lower for every scope without closets inside the walk limit and within noise
  (FORK median <= 1.05 x BASE median + 10 ms) everywhere else, over 8 interleaved rounds whose order alternates
  (ABBA: a fixed BASE-first order measured +6.6 % on a case where both sides run identical code); the walk itself,
  timed directly for every wing with closets, has a median below 5 ms; the walk at the limit (synthetic wing) has a
  median below 0.05 s
  CHECK: bash asgard/gates/search-hot-path/g4_lexical_and_boosts.sh "$PROFILE_PALACE"
  EXPECT: G4_LEXICAL_AND_BOOSTS_IDENTICAL
  EVIDENCE: pending

- [ ] G5: search output identical — the full `tool_search` JSON (minus timing fields) from FORK equals BASE's for the
  G4 query classes on the palace clone and the synthetic palace; no run reports vector search disabled
  CHECK: bash asgard/gates/search-hot-path/g5_search_equivalence.sh "$PROFILE_PALACE"
  EXPECT: G5_SEARCH_IDENTICAL
  EVIDENCE: pending

- [ ] G12: no writer regression — on a working copy, a separate process commits (BEGIN IMMEDIATE / INSERT / COMMIT
  every 50 ms, busy timeout 30 s) while the same side runs the search mix (closet boosts palace-wide and scoped, one
  scoped union-mode drawer read) for 120 s; FORK's writer error count is 0 when BASE's is 0 (never above BASE's),
  FORK's writer p99 commit latency is <= BASE's × 1.10 + 50 ms, and FORK's longest reader SHARED hold (sampled by the
  writer's busy waits) is <= BASE's × 1.10 + 50 ms; median of 3 alternating runs per side
  CHECK: bash asgard/gates/search-hot-path/g12_writer_latency.sh "$PROFILE_PALACE"
  EXPECT: G12_NO_WRITER_REGRESSION
  EVIDENCE: pending

- [ ] G6: upstream test suite green apart from the two failures documented in doc 06; ruff clean on changed files
  CHECK: bash asgard/gates/search-hot-path/g6_suite.sh
  EXPECT: G6_SUITE_OK
  EVIDENCE: pending

- [ ] G7: the design document is one consistent current design, reviewed by three adversarial agents with synthesis
  PASS or CONDITIONAL PASS and every required change folded in (manual review of the document and the review record)
  EVIDENCE: pending
