# Gates: search hot path (wing names, capacity cache, closet boosts)

OWNS: mempalace/backends/chroma_sqlite.py, mempalace/backends/chroma.py, mempalace/search_util.py, mempalace/searcher/**, tests/**, asgard/**

Scope: on the live asgard hub, a search no longer re-runs the unindexed wing-name scan, the capacity probe, or a
palace-wide unbounded closet full-text ranking when nothing in the palace changed, and every search returns exactly
the same drawers in the same order as before.

Environment for every CHECK: macOS asgard; the fork at ~/PycharmProjects/mempalace with its `.venv`; the APFS clone of
the palace at $PROFILE_PALACE (scratch, never the live palace); the live hub only in G9–G10.

- [ ] G1: on the palace clone, the new `sqlite_wing_names` returns exactly the old function's list, its first query's
  plan uses the covering index `embedding_metadata_string_value`, and it takes < 0.05 s (old: 9.3 s measured)
  CHECK: .venv/bin/python -I asgard/gates/search-hot-path/g1_wing_names.py "$PROFILE_PALACE"
  EXPECT: G1_WING_NAMES_OK
  EVIDENCE: pending

- [ ] G2: the capacity verdict and the known-wings set are reused across calls far apart in time (> 10 s / > 30 s,
  simulated clock) while every file they depend on is unchanged, and are recomputed on the first call after any change
  to chroma.sqlite3, its -wal, the segment's pickle or header (unit tests; each test fails on the unmodified tree)
  CHECK: .venv/bin/python -m pytest -q tests/test_search_hot_path_caches.py
  EXPECT: /\d+ passed/
  EVIDENCE: pending

- [ ] G3: negative control — the G2 tests fail on the unmodified v3.12.0 + ef89c2cb tree
  CHECK: bash asgard/gates/search-hot-path/g3_negative_control.sh
  EXPECT: G3_NEGATIVE_CONTROL_OK
  EVIDENCE: pending

- [ ] G4: on the palace clone, closet boosts for the six profiled queries plus 30 further queries (mixed, wing-scoped
  and palace-wide) select the same best-per-source closets as the installed code, or every difference is listed and
  explained in the design's evidence; and the boost step is faster on every query with a common word
  CHECK: bash asgard/gates/search-hot-path/g4_closet_boosts.sh "$PROFILE_PALACE"
  EXPECT: G4_CLOSET_BOOSTS_OK
  EVIDENCE: pending

- [ ] G5: on the palace clone, `mempalace_search` through the fork's tool handler returns the same drawer ids in the
  same order as the installed code for the six profiled queries and 30 further queries
  CHECK: bash asgard/gates/search-hot-path/g5_search_equivalence.sh "$PROFILE_PALACE"
  EXPECT: G5_SEARCH_EQUIVALENT
  EVIDENCE: pending

- [ ] G6: upstream test suite green apart from the two failures documented in doc 06; ruff clean on changed files
  CHECK: bash asgard/gates/search-hot-path/g6_suite.sh
  EXPECT: G6_SUITE_OK
  EVIDENCE: pending

- [ ] G7: POSD design document for the three fixes exists with Design-It-Twice and a 3-agent adversarial review whose
  synthesis is PASS or CONDITIONAL PASS with every required change folded in (manual review of the document)
  EVIDENCE: pending

- [ ] G8: deployed hub is byte-identical to the fork commit (installed package tree == git tree for mempalace/) and the
  patched chromadb bindings sha256 is still 2fedfa99…
  CHECK: bash asgard/gates/search-hot-path/g8_deploy_identity.sh
  EXPECT: G8_DEPLOY_IDENTICAL
  EVIDENCE: pending

- [ ] G9: live hub — the six profiled searches return exactly the baseline drawer ids in order (baseline recorded
  before deployment by hub_searches.py)
  CHECK: ~/.local/share/uv/tools/mempalace/bin/python -I asgard/gates/search-hot-path/hub_searches.py verify "$SEARCH_GATE_DIR"
  EXPECT: SEARCH_RESULTS_IDENTICAL
  EVIDENCE: pending

- [ ] G10: live hub — with searches more than 30 s apart on an unchanged palace, a py-spy sample of the hub shows no
  samples in `sqlite_wing_names` first-query scan or `_hnsw_capacity_status_uncached` after the first search, and the
  hub's `run=` times are recorded with page-in counters (manual review of the profile summary in the evidence doc)
  EVIDENCE: pending

- [ ] G11: docs (asgard/docs/08-search-hot-path.md, README index) committed and pushed; personal-data and AI-mention
  scan clean on everything pushed
  CHECK: bash asgard/gates/search-hot-path/g11_docs_pushed.sh
  EXPECT: G11_DOCS_PUSHED
  EVIDENCE: pending
