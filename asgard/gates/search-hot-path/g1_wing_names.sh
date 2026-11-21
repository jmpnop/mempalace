#!/usr/bin/env bash
# G1: sqlite_wing_names — FORK equals BASE on the palace clone and on a synthetic palace with
# int/float/bool/string wings; FORK's first query uses the partial index; FORK median < 0.05 s.
source "$(dirname "$0")/lib.sh"
FROZEN="${1:?usage: g1_wing_names.sh FROZEN_PALACE}"
ensure_base
before="$(fingerprint "$FROZEN")"
out="$WORK/g1"; rm -rf "$out"; mkdir -p "$out"

base_copy="$(working_copy "$FROZEN" g1-base)"
fork_copy="$(working_copy "$FROZEN" g1-fork)"
for i in 1 2 3 4 5; do
  run_side base wing_names "$base_copy" "$out/base-$i.json" --runs 1
  run_side fork wing_names "$fork_copy" "$out/fork-$i.json" --runs 1
done

synthetic="$WORK/copies/g1-synthetic"
rm -rf "$synthetic"; mkdir -p "$synthetic"
"$PY" -I - "$synthetic" <<'EOF'
import os, sqlite3, sys
conn = sqlite3.connect(os.path.join(sys.argv[1], "chroma.sqlite3"))
conn.executescript("""
CREATE TABLE embedding_metadata (id INTEGER, key TEXT NOT NULL, string_value TEXT, int_value INTEGER,
  float_value REAL, bool_value INTEGER, PRIMARY KEY (id, key));
CREATE INDEX embedding_metadata_string_value ON embedding_metadata (key, string_value)
  WHERE string_value IS NOT NULL;
""")
rows = [(1, "wing", "beta", None, None, None), (2, "wing", None, 7, None, None),
        (3, "wing", None, None, 1.5, None), (4, "wing", None, None, None, 1),
        (5, "wing", "alpha", None, None, None), (6, "wing", "beta", None, None, None),
        (7, "room", "zeta", None, None, None), (8, "wing", "\x01mpesc:a\\0b", None, None, None)]
conn.executemany("INSERT INTO embedding_metadata VALUES (?,?,?,?,?,?)", rows)
conn.commit(); conn.close()
EOF
run_side base wing_names "$synthetic" "$out/base-synthetic.json" --runs 1
run_side fork wing_names "$synthetic" "$out/fork-synthetic.json" --runs 1

after="$(fingerprint "$FROZEN")"
[ "$before" = "$after" ] || die "G1_FAILED the frozen palace changed during the run"

"$PY" -I - "$out" <<'EOF'
import glob, json, os, statistics, sys
out = sys.argv[1]
load = lambda p: json.load(open(p))
base = [load(p) for p in sorted(glob.glob(f"{out}/base-[0-9].json"))]
fork = [load(p) for p in sorted(glob.glob(f"{out}/fork-[0-9].json"))]
ok = True
for b, f in zip(base, fork):
    if b["names"] != f["names"]:
        ok = False; print("MISMATCH palace clone:", b["names"], f["names"])
print("wings on the clone:", len(fork[0]["names"]))
print("BASE first query:", base[0]["first_query"]); print("BASE plan:", base[0]["plan"])
print("FORK first query:", fork[0]["first_query"]); print("FORK plan:", fork[0]["plan"])
if "embedding_metadata_string_value" not in fork[0]["plan"]:
    ok = False; print("FORK first query does not use the partial index")
bt = [t for r in base for t in r["times"]]; ft = [t for r in fork for t in r["times"]]
print(f"BASE median {statistics.median(bt):.4f} s  runs {['%.4f' % t for t in bt]}")
print(f"FORK median {statistics.median(ft):.4f} s  runs {['%.4f' % t for t in ft]}")
if statistics.median(ft) >= 0.05:
    ok = False; print("FORK median not below 0.05 s")
bs, fs = load(f"{out}/base-synthetic.json"), load(f"{out}/fork-synthetic.json")
print("synthetic BASE:", bs["names"], " FORK:", fs["names"])
if bs["names"] != fs["names"]:
    ok = False; print("MISMATCH synthetic")
for r in base: assert "/.worktrees/base-" in r["mempalace_file"], r["mempalace_file"]
for r in fork: assert "/.worktrees/" not in r["mempalace_file"], r["mempalace_file"]
print("G1_WING_NAMES_OK" if ok else "G1_FAILED")
sys.exit(0 if ok else 1)
EOF
