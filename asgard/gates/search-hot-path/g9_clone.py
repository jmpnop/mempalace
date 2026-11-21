"""G9 step 1: a frozen clone of the live palace, taken safely.

usage: g9_clone.py LIVE_PALACE DEST
A separate process (this one) opens chroma.sqlite3 read-only and holds a read transaction only
while ``cp -c`` clones the stat set: chroma.sqlite3, its -wal if present, and every regular file
of every VECTOR segment directory named in ``segments``; palace-level regular files are cloned
too. A clone is discarded and retaken (at most 5 attempts) when a file vanished mid-copy, the
stat set differs before vs after, or a segment's pickle is not its newest file. Prints the
attempts, the stat set, and the embeddings high-water mark for the strict live check.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

live, dest = sys.argv[1:3]
db = os.path.join(live, "chroma.sqlite3")


def stat_set(segments):
    paths = [db, db + "-wal"]
    for seg in segments:
        d = os.path.join(live, seg)
        if os.path.isdir(d):
            paths += [os.path.join(d, n) for n in sorted(os.listdir(d))]
    out = {}
    for p in paths:
        try:
            st = os.stat(p)
            out[os.path.relpath(p, live)] = (st.st_ino, st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            pass
    return out


for attempt in range(1, 6):
    if os.path.exists(dest):
        subprocess.run(["chmod", "-R", "u+w", dest], check=False)
        shutil.rmtree(dest)
    os.makedirs(dest)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, isolation_level=None, timeout=30)
    try:
        conn.execute("BEGIN")
        segments = [r[0] for r in conn.execute("SELECT id FROM segments WHERE scope = 'VECTOR'")]
        high_water = conn.execute("SELECT max(id), count(*) FROM embeddings").fetchone()
        t0 = time.perf_counter()
        before = stat_set(segments)
        copied = True
        for name in sorted(os.listdir(live)):
            src = os.path.join(live, name)
            if name in segments or (os.path.isfile(src) and not name.endswith("-shm")):
                if name.endswith("-journal"):
                    continue
                r = subprocess.run(
                    ["cp", "-cR", src, os.path.join(dest, name)], capture_output=True, text=True
                )
                if r.returncode != 0:
                    copied = False
                    print(f"attempt {attempt}: cp failed for {name}: {r.stderr.strip()}")
                    break
        after = stat_set(segments)
        held = time.perf_counter() - t0
        conn.execute("COMMIT")
    finally:
        conn.close()
    reason = None
    if not copied:
        reason = "copy failed"
    elif before != after:
        reason = "stat set changed: " + ", ".join(
            k for k in set(before) | set(after) if before.get(k) != after.get(k)
        )
    else:
        for seg in segments:
            files = {k: v for k, v in after.items() if k.startswith(seg + os.sep)}
            pickle = os.path.join(seg, "index_metadata.pickle")
            if pickle in files and max(files, key=lambda k: files[k][1]) != pickle:
                reason = f"segment {seg[:8]}: the pickle is not the newest file"
    print(f"attempt {attempt}: read transaction held {held:.3f} s; " + (reason or "accepted"))
    if reason is None:
        no_pickle = [
            s[:8] for s in segments if os.path.join(s, "index_metadata.pickle") not in after
        ]
        print(
            json.dumps(
                {
                    "segments": len(segments),
                    "files": len(after),
                    "segments_without_pickle": no_pickle,
                    "max_embedding_id": high_water[0],
                    "embeddings": high_water[1],
                }
            )
        )
        with open(os.path.join(os.path.dirname(dest.rstrip("/")), "g9-clone.json"), "w") as f:
            json.dump(
                {
                    "max_embedding_id": high_water[0],
                    "embeddings": high_water[1],
                    "no_pickle": no_pickle,
                },
                f,
            )
        sys.exit(0)
print("G9_CLONE_FAILED after 5 attempts")
sys.exit(1)
