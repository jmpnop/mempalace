"""G10 driver: 6 searches through the live hub at least 40 s apart, counting capacity probes.

usage: g10_live_probes.py HUB_LOG LIVE_PALACE OUT_JSON
Before each search it records the stat of every file the capacity fingerprint covers
(chroma.sqlite3, -wal, and each VECTOR segment's index_metadata.pickle and header.bin); after
the run it reads the hub log lines appended since the start.
"""

import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hub_searches

log, live, out = sys.argv[1:4]
GAP = float(os.environ.get("G10_GAP_SECONDS", "40"))
conn = sqlite3.connect(f"file:{os.path.join(live, 'chroma.sqlite3')}?mode=ro", uri=True)
segments = [r[0] for r in conn.execute("SELECT id FROM segments WHERE scope = 'VECTOR'")]
conn.close()
files = [os.path.join(live, "chroma.sqlite3"), os.path.join(live, "chroma.sqlite3-wal")]
files += [
    os.path.join(live, s, n) for s in segments for n in ("index_metadata.pickle", "header.bin")
]


def fingerprint():
    fp = []
    for p in files:
        try:
            st = os.stat(p)
            fp.append((st.st_ino, st.st_mtime_ns, st.st_size))
        except FileNotFoundError:
            fp.append(None)
    return fp


start = os.path.getsize(log)
stats, timings = [], []
for i, query in enumerate(hub_searches.QUERIES):
    if i:
        time.sleep(GAP)
    stats.append(fingerprint())
    t0 = time.perf_counter()
    saved = hub_searches.QUERIES
    hub_searches.QUERIES = [query]
    try:
        result = hub_searches.run_all()[0]
    finally:
        hub_searches.QUERIES = saved
    timings.append({"seconds": round(time.perf_counter() - t0, 2), "results": len(result["ids"])})
stats.append(fingerprint())
time.sleep(2)
with open(log, "rb") as f:
    f.seek(start)
    appended = f.read().decode("utf-8", "replace").splitlines()
json.dump({"stats": stats, "timings": timings, "lines": appended}, open(out, "w"))
