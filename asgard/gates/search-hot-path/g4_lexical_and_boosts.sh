#!/usr/bin/env bash
# G4: sqlite_lexical_rows output and the whole _closet_boosts dict are identical on FORK and BASE
# for every case class, on the palace clone and on the synthetic palace; timings as GATES.md G4.
source "$(dirname "$0")/lib.sh"
FROZEN="${1:?usage: g4_lexical_and_boosts.sh FROZEN_PALACE}"
WALK_LIMIT=20000
ensure_base
before="$(fingerprint "$FROZEN")"
out="$WORK/g4"; rm -rf "$out"; mkdir -p "$out"
cases="$WORK/g4-cases"; synth_frozen="$WORK/g4-synthetic-frozen"
FORK_TREE="$FORK" "$PY" -I "$GATES/g4_cases.py" "$FROZEN" "$synth_frozen" "$cases" "$WALK_LIMIT"

base_clone="$(working_copy "$FROZEN" g4-base)"
fork_clone="$(working_copy "$FROZEN" g4-fork)"
base_syn="$(working_copy "$synth_frozen" g4-base-syn)"
fork_syn="$(working_copy "$synth_frozen" g4-fork-syn)"

echo "equality cases on the clone..."
run_side base lexical "$base_clone" "$out/base-eq.json" --cases "$cases/eq.json"
run_side fork lexical "$fork_clone" "$out/fork-eq.json" --cases "$cases/eq.json"
echo "synthetic cases..."
run_side base lexical "$base_syn" "$out/base-syn.json" --cases "$cases/synthetic.json"
run_side fork lexical "$fork_syn" "$out/fork-syn.json" --cases "$cases/synthetic.json"
echo "timed cases, 8 interleaved rounds, order alternating (ABBA)..."
for i in 1 2 3 4 5 6 7 8; do
  if [ $((i % 4)) = 1 ] || [ $((i % 4)) = 0 ]; then order="base fork"; else order="fork base"; fi
  for side in $order; do
    copy="$base_clone"; [ "$side" = fork ] && copy="$fork_clone"
    run_side "$side" lexical "$copy" "$out/$side-timed-$i.json" --cases "$cases/timed.json" --runs 1
  done
done
echo "walk at the limit..."
run_side fork walk_limit "$fork_syn" "$out/fork-walk.json" --value zz_big --runs 5
echo "walk cost for every wing with closets..."
"$PY" -I - "$cases/timed.json" > "$out/closet-wings.txt" <<'PYEOF'
import json, sys
for c in json.load(open(sys.argv[1])):
    if c["name"].startswith("closets/"):
        print(c["name"].split("/")[1], c["where"]["wing"], sep="\t")
PYEOF
while IFS=$'\t' read -r label value; do
  run_side fork walk_limit "$fork_clone" "$out/fork-walk-$label.json" --value "$value" --runs 9
done < "$out/closet-wings.txt"

after="$(fingerprint "$FROZEN")"
[ "$before" = "$after" ] || die "G4_FAILED the frozen palace changed during the run"
for c in "$base_clone" "$fork_clone" "$base_syn" "$fork_syn"; do chmod -R u+w "$c"; rm -rf "$c"; done

"$PY" -I - "$out" <<'EOF'
import glob, json, statistics, sys
out = sys.argv[1]
load = lambda p: json.load(open(p))
ok = True

def compare(label, b, f):
    global ok
    bc, fc = {c["name"]: c for c in b["cases"]}, {c["name"]: c for c in f["cases"]}
    assert set(bc) == set(fc)
    bad = [n for n in bc if bc[n].get("rows") != fc[n].get("rows") or bc[n].get("boosts") != fc[n].get("boosts")]
    for n in bad:
        print(f"  MISMATCH {label} {n}")
    ok = ok and not bad
    print(f"{label}: {len(bc)} cases, {len(bad)} mismatches; FORK walk outcomes {f.get('outcomes')}")

for side_file in glob.glob(f"{out}/*.json"):
    r = load(side_file)
    want_base = "/base-" in side_file
    assert ("/.worktrees/base-" in r["mempalace_file"]) == want_base, (side_file, r["mempalace_file"])
compare("clone", load(f"{out}/base-eq.json"), load(f"{out}/fork-eq.json"))
compare("synthetic", load(f"{out}/base-syn.json"), load(f"{out}/fork-syn.json"))
base_t = [load(p) for p in sorted(glob.glob(f"{out}/base-timed-*.json"))]
fork_t = [load(p) for p in sorted(glob.glob(f"{out}/fork-timed-*.json"))]
for i, (b, f) in enumerate(zip(base_t, fork_t), 1):
    compare(f"timed run {i}", b, f)
print("timed medians (s):")
for name in [c["name"] for c in base_t[0]["cases"]]:
    tb = [t for r in base_t for c in r["cases"] if c["name"] == name for t in c["times"]]
    tf = [t for r in fork_t for c in r["cases"] if c["name"] == name for t in c["times"]]
    mb, mf = statistics.median(tb), statistics.median(tf)
    if name.startswith("noclosets-inlimit/"):
        good, rule = mf < mb, "lower"
    else:
        good, rule = mf <= mb * 1.05 + 0.01, "<= 1.05 x BASE + 10 ms"
    ok = ok and good
    print(f"  {'ok ' if good else 'BAD'} {name:38s} BASE {mb:8.4f}  FORK {mf:8.4f}  ({rule})")
for p in sorted(glob.glob(f"{out}/fork-walk-wing*.json")):
    r = load(p)
    label = p.rsplit("fork-walk-", 1)[1][:-5]
    good = r["result"] == "hit" and r["median"] < 0.005
    ok = ok and good
    print(f"  {'ok ' if good else 'BAD'} walk cost {label}: {r['result']}, median {r['median'] * 1000:.3f} ms")
w = load(f"{out}/fork-walk.json")
print(f"walk at the limit: result {w['result']}, median {w['median']:.4f} s, runs {['%.4f' % t for t in w['times']]}")
ok = ok and w["result"] == "limit" and w["median"] < 0.05
print("G4_LEXICAL_AND_BOOSTS_IDENTICAL" if ok else "G4_FAILED")
sys.exit(0 if ok else 1)
EOF
