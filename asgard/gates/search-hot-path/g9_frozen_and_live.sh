#!/usr/bin/env bash
# G9: frozen-state equivalence at deploy time, then the live hub. See GATES.md G9.
source "$(dirname "$0")/lib.sh"
LIVE="${LIVE_PALACE:-$HOME/.config/mempalace/palace}"
ensure_base
out="$WORK/g9"; frozen="$out/frozen-live"
mkdir -p "$out"
"$PY" -I "$GATES/g9_clone.py" "$LIVE" "$frozen" | tee "$out/clone.log"
"$PY" -I - "$frozen" <<'EOF'
import os, sqlite3, sys, time
t0 = time.perf_counter()
conn = sqlite3.connect(f"file:{os.path.join(sys.argv[1], 'chroma.sqlite3')}?mode=ro", uri=True)
rows = conn.execute("PRAGMA quick_check").fetchall()
print(f"quick_check on the copy ({time.perf_counter() - t0:.0f} s): {rows[:3]}")
sys.exit(0 if rows == [("ok",)] else 1)
EOF
cap_copy="$(working_copy "$frozen" g9-capacity)"
run_side fork capacity "$cap_copy" "$out/capacity.json"
chmod -R u+w "$cap_copy"; rm -rf "$cap_copy"
chmod -R a-w "$frozen"
"$PY" -I - "$GATES" "$out/six.json" <<'EOF'
import json, sys
sys.path.insert(0, sys.argv[1])
from hub_searches import QUERIES
json.dump([dict(q, name=f"six/{i}") for i, q in enumerate(QUERIES)], open(sys.argv[2], "w"))
EOF
for side in base fork; do
  copy="$(working_copy "$frozen" "g9-$side")"
  run_side "$side" search "$copy" "$out/$side.json" --cases "$out/six.json"
  chmod -R u+w "$copy"; rm -rf "$copy"
done
"$PY" -I - "$GATES" "$out/live.json" <<'EOF'
import json, sys
sys.path.insert(0, sys.argv[1])
from hub_searches import run_all
json.dump(run_all(), open(sys.argv[2], "w"))
EOF
"$PY" -I - "$LIVE" "$out" <<'EOF'
import json, os, sqlite3, sys
live, out = sys.argv[1:3]
ok = True
clone = json.load(open(f"{out}/g9-clone.json"))
cap = json.load(open(f"{out}/capacity.json"))
for name, st in cap["status"].items():
    print(f"capacity on the copy: {name} {st['status']} (sqlite {st.get('sqlite_count')}, hnsw {st.get('hnsw_count')})")
    ok = ok and st["status"] == "ok"
for seg, n in cap["pickle_counts"].items():
    if n is None and seg[:8] not in clone["no_pickle"]:
        ok = False; print(f"segment {seg[:8]}: pickle does not unpickle on the copy")
    print(f"segment {seg[:8]}: pickle elements {n}")
b, f = (json.load(open(f"{out}/{s}.json")) for s in ("base", "fork"))
assert "/.worktrees/base-" in b["mempalace_file"] and "/.worktrees/" not in f["mempalace_file"]
fork_ids = []
for bc, fc in zip(b["cases"], f["cases"]):
    same = bc["result"] == fc["result"]
    bad = (not same) or bc["vector_disabled"] or fc["vector_disabled"] or "error" in fc["result"]
    ids = [h.get("drawer_id") for h in fc["result"].get("results", [])]
    fork_ids.append(ids)
    print(f"  {'BAD' if bad else 'ok '} frozen {fc['name']}: identical={same} results={len(ids)}")
    ok = ok and not bad
live_runs = json.load(open(f"{out}/live.json"))
conn = sqlite3.connect(f"file:{os.path.join(live, 'chroma.sqlite3')}?mode=ro", uri=True)
now = conn.execute("SELECT max(id), count(*) FROM embeddings").fetchone()
conn.close()
strict = list(now) == [clone["max_embedding_id"], clone["embeddings"]]
for i, r in enumerate(live_runs):
    good = bool(r["ids"])
    line = f"  {'ok ' if good else 'BAD'} live six/{i}: {len(r['ids'])} results in {r['seconds']} s"
    if strict:
        same = r["ids"] == fork_ids[i]
        good = good and same
        line += f", equal to frozen FORK: {same}"
    print(line)
    ok = ok and good
print("strict live-equals-frozen check ran" if strict else
      f"rows changed since the clone ({clone['embeddings']} -> {now[1]}): live smoke check only")
print("G9_FROZEN_IDENTICAL_LIVE_OK" if ok else "G9_FAILED")
sys.exit(0 if ok else 1)
EOF
