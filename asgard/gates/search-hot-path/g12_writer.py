"""G12 writer: commit to the palace copy every 50 ms from a separate process until told to stop.

usage: g12_writer.py DB STOP_FILE OUT_JSON
Each commit is BEGIN IMMEDIATE / INSERT / COMMIT on a scratch table with a 30 s busy timeout,
the way a hook or CLI mine writes from another process. Records the latency of each commit
(start of BEGIN to end of COMMIT) and every sqlite3 error.
"""

import json
import os
import sqlite3
import sys
import time

db, stop_file, out = sys.argv[1:4]
conn = sqlite3.connect(db, timeout=30.0, isolation_level=None)
conn.execute("CREATE TABLE IF NOT EXISTS g12_writes (id INTEGER PRIMARY KEY, t REAL)")
latencies, errors = [], []
while not os.path.exists(stop_file):
    t0 = time.perf_counter()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO g12_writes (t) VALUES (?)", (time.time(),))
        conn.execute("COMMIT")
        latencies.append(time.perf_counter() - t0)
    except sqlite3.Error as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
    time.sleep(0.05)
conn.close()
with open(out, "w") as f:
    json.dump({"latencies": latencies, "errors": errors}, f)
