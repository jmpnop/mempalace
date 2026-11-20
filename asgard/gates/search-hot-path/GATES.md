# Gates: search hot path (wing names, capacity cache, closet boosts)

OWNS: mempalace/backends/chroma_sqlite.py, mempalace/backends/chroma.py, mempalace/search_util.py, mempalace/searcher/**, tests/**, asgard/**

Scope: on the live asgard hub a search no longer runs the unindexed wing-name scan, re-probes HNSW capacity when the
verdict's files and age allow reuse, or runs a palace-wide closet full-text query for a scope with no closets; every
search returns exactly the same output as before (revision 2 of the gates, after design review round 1).

Environment for every CHECK: macOS asgard; the fork checkout with its `.venv`; the APFS clone of
the palace at $PROFILE_PALACE (scratch, never the live palace); the live hub only in G9–G10.

- [ ] G1: on the palace clone, the new `sqlite_wing_names` returns exactly the old function's list, its first query's
  plan uses the covering index `embedding_metadata_string_value`, and it takes < 0.05 s (old: 9.3 s measured)
  CHECK: .venv/bin/python -I asgard/gates/search-hot-path/g1_wing_names.py "$PROFILE_PALACE"
  EXPECT: G1_WING_NAMES_OK
  EVIDENCE: pending

- [ ] G2: capacity-verdict reuse policy (unit tests, simulated wall clock and filesystem type): on an allow-listed
  filesystem a verdict is reused past 10 s while its files are unchanged; on any other filesystem it is re-probed after
  10 s as before; an age-dependent verdict (no explicit sync threshold, 0 < divergence) is re-probed no later than the
  moment its pickle reaches the grace age; a verdict without an HNSW count is re-probed after 10 s; any change to a
  fingerprinted file re-probes immediately
  CHECK: .venv/bin/python -m pytest -q tests/test_hnsw_capacity.py tests/test_search_hot_path.py
  EXPECT: /\d+ passed/
  EVIDENCE: pending

- [ ] G3: negative control — the new tests of G2 and G4u fail on the unmodified v3.12.0 + ef89c2cb tree, and pass on
  the fork
  CHECK: bash asgard/gates/search-hot-path/g3_negative_control.sh
  EXPECT: G3_NEGATIVE_CONTROL_OK
  EVIDENCE: pending

- [ ] G4: exact closet-boost equality — for every query class (wing without closets, wing with closets of each size
  band, palace-wide, `$and` wing+room, `$in` aliases, an AAAK-refetch case, a query with no token of 3+ characters),
  on the palace clone and on a synthetic chroma-shaped palace holding int-, float- and escape-encoded wing values, the
  whole `_closet_boosts` dict (rank, distance, preview per source) from the fork equals the installed code's; and the
  boost step is faster for every scope without closets, with no regression > 5 % elsewhere
  CHECK: bash asgard/gates/search-hot-path/g4_closet_boosts.sh "$PROFILE_PALACE"
  EXPECT: G4_CLOSET_BOOSTS_IDENTICAL
  EVIDENCE: pending

- [ ] G5: exact search equality — the full `tool_search` JSON (minus timing fields) from the fork equals the installed
  code's for the same query classes on the palace clone and the synthetic palace
  CHECK: bash asgard/gates/search-hot-path/g5_search_equivalence.sh "$PROFILE_PALACE"
  EXPECT: G5_SEARCH_IDENTICAL
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

- [ ] G9: frozen-state equivalence at deploy time — on a fresh APFS clone of the live palace taken right before
  deployment, the installed code and the fork return identical full `tool_search` JSON for the six profiled queries;
  after deployment the live hub answers the six queries with no `error` key and a non-empty `results`, and if no row
  was inserted between the clone and the live run (`MAX(seq_id)` of `embeddings_queue` and the chroma.sqlite3 stat
  unchanged) the live drawer ids equal the clone's
  CHECK: bash asgard/gates/search-hot-path/g9_frozen_and_live.sh
  EXPECT: G9_FROZEN_IDENTICAL_LIVE_OK
  EVIDENCE: pending

- [ ] G10: live hub — a py-spy sample of the hub over several searches shows (a) no sample in the first-query table
  scan of `sqlite_wing_names`, and (b) `_hnsw_capacity_status_uncached` only on searches that follow a change of a
  fingerprinted file or the end of the verdict's reuse window (re-probes counted against the observed signature
  changes and reuse deadlines); page-in counters recorded for each search (manual review of the profile summary)
  EVIDENCE: pending

- [ ] G11: docs (asgard/docs/08-search-hot-path.md, README index) committed and pushed; personal-data and AI-mention
  scan clean on everything pushed
  CHECK: bash asgard/gates/search-hot-path/g11_docs_pushed.sh
  EXPECT: G11_DOCS_PUSHED
  EVIDENCE: pending
