# Gates: search hot path, after the deploy (G8, G10, G9, G10p, G11)

OWNS: mempalace/backends/chroma_sqlite.py, mempalace/backends/chroma.py, mempalace/mcp_server/_guards.py, tests/**, asgard/**

Scope: the gates of GATES.md that can only pass on the deployed hub, in run order: deploy identity first, then the
live probe count while the restarted hub still has its first capacity probes to log, then frozen-state equivalence,
the live profile and the docs. Run after every gate in GATES.md is met and the hub is redeployed.

- [ ] G8: deployed hub is byte-identical to the fork commit (installed package tree == git tree for mempalace/) and the
  patched chromadb bindings sha256 is still 2fedfa99…
  CHECK: bash asgard/gates/search-hot-path/g8_deploy_identity.sh
  EXPECT: G8_DEPLOY_IDENTICAL
  EVIDENCE: pending

- [ ] G10: live probe count — after a positive control proves an INFO line from `mempalace.backends.chroma` reaches
  `hub.log` (the script fails if no such line appears), over 6 searches at least 40 s apart on the deployed hub the
  new `hnsw capacity probe` lines number at most one per collection plus one per recorded change of the fingerprinted
  files (stat of the fingerprint set recorded before each search); the script prints every line with its reason
  CHECK: bash asgard/gates/search-hot-path/g10_live_probe_count.sh
  EXPECT: G10_LIVE_PROBES_BOUNDED
  EVIDENCE: pending

- [ ] G9: frozen-state equivalence at deploy time — a frozen clone of the live palace taken by the safe procedure:
  a separate process opens `chroma.sqlite3` read-only and holds a read transaction only while `cp -c` clones the
  stat set (`chroma.sqlite3`, `chroma.sqlite3-wal` if present, and every regular file of every VECTOR segment
  directory named in `segments`); a clone that fails because a file vanished mid-copy, or whose stat set differs
  before vs after, is discarded and retaken (at most 5 attempts, each printed); in every segment that has a pickle
  the pickle is the newest file; the read transaction is released; only then, on the copy: `quick_check` ok, every
  pickle unpickles, and `hnsw_capacity_status` on the copy returns "ok" for drawers and closets. A segment without a
  pickle is accepted only if it had none in the live stat set too (printed). The frozen copy is made read-only.
  BASE and FORK each on their own working copy return identical full `tool_search` JSON for the six profiled
  queries, none with vector search disabled or an `error`; after deployment the live hub answers the six with no
  `error` and non-empty results; the script prints whether the strict live-equals-frozen check ran (only when no
  row was inserted since the clone) or only the smoke check
  CHECK: bash asgard/gates/search-hot-path/g9_frozen_and_live.sh
  EXPECT: G9_FROZEN_IDENTICAL_LIVE_OK
  EVIDENCE: pending

- [ ] G10p: live profile — a nonblocking py-spy sample of the deployed hub across the same 6 searches shows no frame
  in `sqlite_wing_names` above 1 % of samples and no `_hnsw_capacity_status_uncached` frame outside searches that
  G10 counted as probes (manual review of the profile summary in the evidence doc)
  EVIDENCE: pending

- [ ] G11: docs (asgard/docs/08-search-hot-path.md, README index) committed and pushed; personal-data and AI-mention
  scan clean on everything pushed
  CHECK: bash asgard/gates/search-hot-path/g11_docs_pushed.sh
  EXPECT: G11_DOCS_PUSHED
  EVIDENCE: pending
