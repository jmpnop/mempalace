#!/usr/bin/env bash
# G5: the full tool_search JSON (timing fields removed) from FORK equals BASE's for the G4 query
# classes that tool_search can express, on the palace clone and on a synthetic palace (the clone
# plus closet rows: AAAK summaries, an escaped wing, int and string "7" wings, a Greek closet;
# no synthetic drawers, which would have no vectors); no run reports vector search disabled.
source "$(dirname "$0")/lib.sh"
FROZEN="${1:?usage: g5_search_equivalence.sh FROZEN_PALACE}"
ensure_base
before="$(fingerprint "$FROZEN")"
out="$WORK/g5"; rm -rf "$out"; mkdir -p "$out"
cases="$WORK/g5-cases"; synth_frozen="$WORK/g5-synthetic-frozen"
G4_BIG_ROWS=0 FORK_TREE="$FORK" "$PY" -I "$GATES/g4_cases.py" "$FROZEN" "$synth_frozen" "$cases" 20000 > "$out/classes.txt"
"$PY" -I - "$FROZEN" "$cases" <<'EOF'
import json, os, sqlite3, sys
frozen, cases = sys.argv[1:3]
conn = sqlite3.connect(f"file:{os.path.join(frozen, 'chroma.sqlite3')}?mode=ro", uri=True)
sizes = {}
for wing, coll, n in conn.execute(
    """SELECT m.string_value, c.name, count(*) FROM embedding_metadata m JOIN embeddings e ON e.id = m.id
       JOIN segments s ON s.id = e.segment_id JOIN collections c ON c.id = s.collection
       WHERE m.key = 'wing' AND m.string_value IS NOT NULL GROUP BY 1, 2"""):
    d = sizes.setdefault(wing, {})
    d[coll] = n
wings = sorted(sizes)
labels = {w: f"wing{n:02d}" for n, w in enumerate(wings)}
closets = sorted((w for w in wings if sizes[w].get("mempalace_closets")), key=lambda w: sizes[w]["mempalace_closets"])
without = [w for w in wings if not sizes[w].get("mempalace_closets") and sizes[w].get("mempalace_drawers")]
Q = ["mine lock yield during integrity check", "chromadb write bindings hold the GIL", "Rust port of the bottleneck",
     "slow MCP saves session client rebuild", "hub restart launchd", "corpus markdown conversion pandoc"]
room = conn.execute("""SELECT r.string_value FROM embedding_metadata w JOIN embedding_metadata r ON r.id = w.id
    AND r.key = 'room' WHERE w.key = 'wing' AND w.string_value = ? LIMIT 1""", (closets[-1],)).fetchone()[0]
c = []
for qi, q in enumerate(Q):
    c.append({"name": f"palace-wide/q{qi}", "query": q, "limit": 5})
for w in without[:6] + closets:
    kind = "closets" if w in closets else "noclosets"
    c.append({"name": f"{kind}/{labels[w]}/q1", "query": Q[1], "wing": w, "limit": 5})
    c.append({"name": f"{kind}/{labels[w]}/q4", "query": Q[4], "wing": w, "limit": 5})
c += [
    {"name": "wing-room/closets", "query": Q[4], "wing": closets[-1], "room": room, "limit": 5},
    {"name": "no-token/closets", "query": "a b", "wing": closets[-1], "limit": 5},
    {"name": "greek/palace-wide", "query": "λόγος σοφίας", "limit": 5},
    {"name": "missing-wing", "query": Q[1], "wing": "zz_no_such_wing", "limit": 5},
    {"name": "union/noclosets", "query": Q[2], "wing": without[0], "limit": 5, "candidate_strategy": "union"},
    {"name": "union/closets", "query": Q[2], "wing": closets[-1], "limit": 5, "candidate_strategy": "union"},
]
syn = [
    {"name": "syn/aaak-wing", "query": Q[4], "wing": "zz_aaak", "limit": 5},
    {"name": "syn/str7-wing", "query": Q[4], "wing": "7", "limit": 5},
    {"name": "syn/greek-wing", "query": "λόγος σοφίας", "wing": "zz_greek", "limit": 5},
    {"name": "syn/palace-wide", "query": Q[4], "limit": 5},
    {"name": "syn/noclosets", "query": Q[4], "wing": without[0], "limit": 5},
]
json.dump(c, open(os.path.join(cases, "search.json"), "w"))
json.dump(syn, open(os.path.join(cases, "search-syn.json"), "w"))
print(f"search cases: clone {len(c)}, synthetic {len(syn)}")
EOF
for side in base fork; do
  copy="$(working_copy "$FROZEN" "g5-$side")"
  run_side "$side" search "$copy" "$out/$side.json" --cases "$cases/search.json"
  chmod -R u+w "$copy"; rm -rf "$copy"
  copy="$(working_copy "$synth_frozen" "g5-$side-syn")"
  run_side "$side" search "$copy" "$out/$side-syn.json" --cases "$cases/search-syn.json"
  chmod -R u+w "$copy"; rm -rf "$copy"
done
after="$(fingerprint "$FROZEN")"
[ "$before" = "$after" ] || die "G5_FAILED the frozen palace changed during the run"

"$PY" -I - "$out" <<'EOF'
import json, sys
out = sys.argv[1]
ok = True
for label, suffix in (("clone", ""), ("synthetic", "-syn")):
    b, f = (json.load(open(f"{out}/{s}{suffix}.json")) for s in ("base", "fork"))
    assert "/.worktrees/base-" in b["mempalace_file"] and "/.worktrees/" not in f["mempalace_file"]
    bad = 0
    for bc, fc in zip(b["cases"], f["cases"]):
        assert bc["name"] == fc["name"]
        same = bc["result"] == fc["result"]
        err = "error" in fc["result"] or "error" in bc["result"]
        disabled = bc["vector_disabled"] or fc["vector_disabled"]
        hits = len(fc["result"].get("results", [])) if isinstance(fc["result"], dict) else -1
        flag = "ok " if same and not disabled and not err else "BAD"
        if flag == "BAD":
            bad += 1
        print(f"  {flag} {label:9s} {bc['name']:30s} results={hits} identical={same} error={err} vector_disabled={disabled}")
    ok = ok and bad == 0
    print(f"{label}: {len(b['cases'])} cases, {bad} bad")
print("G5_SEARCH_IDENTICAL" if ok else "G5_FAILED")
sys.exit(0 if ok else 1)
EOF
