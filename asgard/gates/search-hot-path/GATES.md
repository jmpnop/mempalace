# Gates: search hot path (wing names, capacity verdict reuse, empty-scope lexical skip)

OWNS: mempalace/backends/chroma_sqlite.py, mempalace/backends/chroma.py, mempalace/mcp_server/_guards.py, tests/**, asgard/**

Scope: on the live asgard hub a search no longer runs the unindexed wing-name scan, re-probes HNSW capacity while the
verdict's files, reuse window and age deadline allow reuse, or runs a full-text query for a scope that holds no row of
the collection; every search returns exactly the same output as before. (Gates revision 3, matching design revision 3.)

Conventions for every CHECK (enforced inside the scripts, each of which prints its own diagnosis):
- BASE = a `git worktree` of the deployed commit ef89c2cb; FORK = the branch head. Both run with the same interpreter
  and chromadb build (the fork's `.venv`), swapping only the `mempalace` source via `PYTHONPATH`; each run asserts
  `mempalace.__file__` lies in the tree it means to test.
- Every comparison runs each side on its own fresh writable APFS working copy (`cp -cR`) of a frozen, read-only clone;
  the frozen clone's fingerprint (stat of every file) is checked unchanged afterwards. Config points at the copy.
- Timings: median of >= 5 interleaved warm runs per case (one discarded warm-up), never single runs.

- [ ] G1: wing names — on the palace clone FORK's `sqlite_wing_names` equals BASE's list, its first query's plan uses
  `embedding_metadata_string_value`, and its median time is < 0.05 s; a synthetic palace with int, float, bool and
  string wings gives equal lists too
  CHECK: bash asgard/gates/search-hot-path/g1_wing_names.sh "$PROFILE_PALACE"
  EXPECT: G1_WING_NAMES_OK
  EVIDENCE: pending

- [ ] G2: capacity reuse rule (unit tests, simulated clocks, mocked filesystem type): non-allow-listed fs re-probes
  after 10 s; allow-listed fs reuses past 10 s with unchanged files; `hnsw_count is None` re-probes after 10 s;
  implicit-threshold "ok" with divergence re-probes at pickle mtime + 300 s; explicit threshold with divergence and a
  pickle older than 300 s is reused past 10 s on an allow-listed fs; an already-diverged verdict is reused within its
  window; any fingerprint change re-probes at once; a backward wall-clock step does not extend the reuse window
  CHECK: .venv/bin/python -m pytest -q tests/test_hnsw_capacity.py
  EXPECT: /\d+ passed/
  EVIDENCE: pending

- [ ] G2r: the real (unmocked) `_fs_type` returns "apfs" for the palace clone's directory and for pytest's tmp dir on
  this Intel Mac (the `statfs$INODE64` path), and "" for a path that does not exist
  CHECK: .venv/bin/python -I asgard/gates/search-hot-path/g2r_fs_type.py "$PROFILE_PALACE"
  EXPECT: G2R_FS_TYPE_OK
  EVIDENCE: pending

- [ ] G3: negative control — exactly these new tests fail on BASE and pass on FORK: allow-listed reuse past 10 s;
  explicit-threshold old-pickle reuse; implicit-threshold flips_at re-probe timing; wing-name first-query plan uses the
  index; empty-scope skip runs no MATCH; bounded walk falls back at the limit; probe error falls back. The
  behaviour-preserving tests (10 s re-probe off the allow-list, fingerprint change) pass on both
  CHECK: bash asgard/gates/search-hot-path/g3_negative_control.sh
  EXPECT: G3_NEGATIVE_CONTROL_OK
  EVIDENCE: pending

- [ ] G4: lexical rows and closet boosts identical — for these classes: wing without closets (small and a synthetic
  600,000-row one past the walk limit), wing with closets of each size band, palace-wide, `$and` wing+room, `$in`
  aliases, an AAAK-refetch case, a query with no 3+-character token, a non-ASCII query (Greek final sigma), escaped
  wing values, numeric wing values — FORK's `sqlite_lexical_rows` output and whole `_closet_boosts` dict (rank,
  distance, preview per source) equal BASE's on the palace clone and the synthetic palace; median boost time is lower
  for every scope without closets inside the walk limit and within noise (<= 5 % on medians) everywhere else
  CHECK: bash asgard/gates/search-hot-path/g4_lexical_and_boosts.sh "$PROFILE_PALACE"
  EXPECT: G4_LEXICAL_AND_BOOSTS_IDENTICAL
  EVIDENCE: pending

- [ ] G5: search output identical — the full `tool_search` JSON (minus timing fields) from FORK equals BASE's for the
  G4 query classes on the palace clone and the synthetic palace; no run reports vector search disabled
  CHECK: bash asgard/gates/search-hot-path/g5_search_equivalence.sh "$PROFILE_PALACE"
  EXPECT: G5_SEARCH_IDENTICAL
  EVIDENCE: pending

- [ ] G6: upstream test suite green apart from the two failures documented in doc 06; ruff clean on changed files
  CHECK: bash asgard/gates/search-hot-path/g6_suite.sh
  EXPECT: G6_SUITE_OK
  EVIDENCE: pending

- [ ] G7: the design document is one consistent current design, reviewed by three adversarial agents with synthesis
  PASS or CONDITIONAL PASS and every required change folded in (manual review of the document and the review record)
  EVIDENCE: pending

- [ ] G8: deployed hub is byte-identical to the fork commit (installed package tree == git tree for mempalace/) and the
  patched chromadb bindings sha256 is still 2fedfa99…
  CHECK: bash asgard/gates/search-hot-path/g8_deploy_identity.sh
  EXPECT: G8_DEPLOY_IDENTICAL
  EVIDENCE: pending

- [ ] G9: frozen-state equivalence at deploy time — a frozen clone of the live palace taken by the safe procedure
  (separate process holding a read transaction on `chroma.sqlite3`; `clonefile` of the database, `-wal` if present,
  every VECTOR segment directory and the palace-level files; stat fingerprint before == after; in every segment the
  pickle is the newest file; `quick_check` ok; capacity verdict "ok" and equal to the live hub's; frozen copy made
  read-only); BASE and FORK each on their own working copy return identical full `tool_search` JSON for the six
  profiled queries, none with vector search disabled or an `error`; after deployment the live hub answers the six with
  no `error` and non-empty results; the script prints whether the strict live-equals-frozen check ran (only when no
  row was inserted since the clone) or only the smoke check
  CHECK: bash asgard/gates/search-hot-path/g9_frozen_and_live.sh
  EXPECT: G9_FROZEN_IDENTICAL_LIVE_OK
  EVIDENCE: pending

- [ ] G10: live cache behaviour — over 6 searches at least 40 s apart on the deployed hub, a py-spy sample shows no
  sample in the first-query table scan of `sqlite_wing_names`, and `_hnsw_capacity_status_uncached` runs at most once
  plus once per observed fingerprint change (each counted against a recorded stat of the fingerprinted files);
  page-in counters recorded per search (manual review of the profile summary in the evidence doc)
  EVIDENCE: pending

- [ ] G11: docs (asgard/docs/08-search-hot-path.md, README index) committed and pushed; personal-data and AI-mention
  scan clean on everything pushed
  CHECK: bash asgard/gates/search-hot-path/g11_docs_pushed.sh
  EXPECT: G11_DOCS_PUSHED
  EVIDENCE: pending
