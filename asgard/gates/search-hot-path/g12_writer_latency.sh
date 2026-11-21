#!/usr/bin/env bash
# G12: no writer regression. A separate process commits every 50 ms while one side runs the
# search mix (closet boosts palace-wide, scoped to the largest closet wing, scoped to a closet-less
# wing, and one scoped union-mode drawer read) for 120 s; 3 alternating runs per side.
source "$(dirname "$0")/lib.sh"
FROZEN="${1:?usage: g12_writer_latency.sh FROZEN_PALACE}"
DURATION="${G12_DURATION:-120}"
ensure_base
before="$(fingerprint "$FROZEN")"
out="$WORK/g12"; rm -rf "$out"; mkdir -p "$out"
"$PY" -I - "$FROZEN" "$out/mix.json" <<'EOF'
import json, os, sqlite3, sys
frozen, path = sys.argv[1:3]
conn = sqlite3.connect(f"file:{os.path.join(frozen, 'chroma.sqlite3')}?mode=ro", uri=True)
rows = conn.execute(
    """SELECT m.string_value, sum(c.name = 'mempalace_closets'), count(*) FROM embedding_metadata m
       JOIN embeddings e ON e.id = m.id JOIN segments s ON s.id = e.segment_id
       JOIN collections c ON c.id = s.collection
       WHERE m.key = 'wing' AND m.string_value IS NOT NULL GROUP BY 1""").fetchall()
closets = max((r for r in rows if r[1]), key=lambda r: r[1])[0]
free = [r for r in rows if not r[1]]
small_free = min(free, key=lambda r: r[2])[0]
big_free = max(free, key=lambda r: r[2])[0]
q = "chromadb write bindings hold the GIL"
json.dump([
    {"name": "palace-wide", "query": "Rust port of the bottleneck"},
    {"name": "closets-largest", "query": q, "where": {"wing": closets}},
    {"name": "noclosets-small", "query": q, "where": {"wing": small_free}},
    {"name": "union-drawers-largest-free", "query": "Rust port of the bottleneck",
     "collection": "mempalace_drawers", "where": {"wing": big_free}},
], open(path, "w"))
EOF

run_once() {  # side n
  local copy; copy="$(working_copy "$FROZEN" "g12-$1-$2")"
  local stop="$out/stop-$1-$2"
  "$PY" -I "$GATES/g12_writer.py" "$copy/chroma.sqlite3" "$stop" "$out/writer-$1-$2.json" &
  local writer=$!
  run_side "$1" mix "$copy" "$out/mix-$1-$2.json" --cases "$out/mix.json" --duration "$DURATION"
  touch "$stop"; wait "$writer"
  chmod -R u+w "$copy"; rm -rf "$copy"
}
for n in 1 2 3; do run_once base "$n"; run_once fork "$n"; done
after="$(fingerprint "$FROZEN")"
[ "$before" = "$after" ] || die "G12_FAILED the frozen palace changed during the run"

"$PY" -I - "$out" <<'EOF'
import json, statistics, sys
out = sys.argv[1]
def pct(xs, p):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]
stats = {}
for side in ("base", "fork"):
    runs = []
    for n in (1, 2, 3):
        w = json.load(open(f"{out}/writer-{side}-{n}.json"))
        m = json.load(open(f"{out}/mix-{side}-{n}.json"))
        lat = w["latencies"]
        runs.append({"commits": len(lat), "errors": len(w["errors"]), "p99": pct(lat, 99) if lat else 1e9,
                     "max": max(lat) if lat else 1e9, "reads": len(m["ran"])})
        print(f"  {side} run {n}: commits {len(lat)}, errors {len(w['errors'])}, p50 {pct(lat, 50) * 1000:.1f} ms,"
              f" p99 {runs[-1]['p99'] * 1000:.1f} ms, max {runs[-1]['max'] * 1000:.1f} ms, searches {len(m['ran'])}")
    stats[side] = {k: statistics.median(r[k] for r in runs) for k in ("commits", "errors", "p99", "max", "reads")}
    stats[side]["errors_total"] = sum(r["errors"] for r in runs)
b, f = stats["base"], stats["fork"]
print(f"medians BASE {b}\nmedians FORK {f}")
ok = (f["errors_total"] <= b["errors_total"]) and (b["errors_total"] > 0 or f["errors_total"] == 0)
ok = ok and f["p99"] <= b["p99"] * 1.10 + 0.05 and f["max"] <= b["max"] * 1.10 + 0.05
print("G12_NO_WRITER_REGRESSION" if ok else "G12_FAILED")
sys.exit(0 if ok else 1)
EOF
