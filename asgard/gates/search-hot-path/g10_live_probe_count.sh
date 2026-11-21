#!/usr/bin/env bash
# G10: on the deployed hub, capacity probes over 6 searches >= 40 s apart number at most one per
# collection plus one per collection for each recorded change of the fingerprinted files; a
# positive control requires that the hub log carries probe lines at all.
source "$(dirname "$0")/lib.sh"
LOG="${HUB_LOG:-$HOME/.config/mempalace/hub.log}"
LIVE="${LIVE_PALACE:-$HOME/.config/mempalace/palace}"
out="$WORK/g10"; mkdir -p "$out"
"$PY" -I "$GATES/g10_live_probes.py" "$LOG" "$LIVE" "$out/run.json"
"$PY" -I - "$out/run.json" <<'EOF'
import json, re, sys
r = json.load(open(sys.argv[1]))
probes = [l for l in r["lines"] if "hnsw capacity probe:" in l]
walks = [l for l in r["lines"] if "scope walk:" in l]
changes = sum(1 for a, b in zip(r["stats"], r["stats"][1:]) if a != b)
collections = sorted({m.group(1) for l in probes for m in [re.search(r"probe: (\S+) reason=", l)] if m})
for i, t in enumerate(r["timings"]):
    print(f"search {i}: {t['seconds']} s, {t['results']} results")
for l in probes + walks:
    print("  log:", l.strip()[-120:])
allowed = max(len(collections), 1) * (1 + changes)
print(f"probe lines {len(probes)} over collections {collections}; fingerprint changes between searches {changes};"
      f" allowed {allowed}")
ok = bool(probes) and len(probes) <= allowed and all(t["results"] > 0 for t in r["timings"])
if not probes:
    print("positive control failed: no probe line reached the hub log (logging not wired, or no probe ran)")
print("G10_LIVE_PROBES_BOUNDED" if ok else "G10_FAILED")
sys.exit(0 if ok else 1)
EOF
