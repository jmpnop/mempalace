"""q1_inproc.py SCRATCH_DIR — in-process gate on a throwaway palace (never the live one).

Run with MEMPALACE_CONFIG_DIR / MEMPALACE_PALACE_PATH pointing into SCRATCH_DIR (q1.sh does that).
"""
import json, os, shutil, sqlite3, sys, threading, time

scratch = sys.argv[1]
palace = os.environ["MEMPALACE_PALACE_PATH"]
assert palace.startswith(scratch), "refusing to run outside the scratch palace"
sys.argv = ["mempalace-mcp"]
import mempalace.mcp_server as s  # noqa: E402
from mempalace import hub_lock  # noqa: E402

T = s.TOOLS
fails = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""), flush=True)
    if not cond:
        fails.append(name)


def call(tool, **kw):
    r = T[tool]["handler"](**kw)
    return r if isinstance(r, dict) else json.loads(r)


def add(text):
    r = call("mempalace_add_drawer", wing="q1", room="probe", content=text, added_by="q1-gate")
    return r.get("drawer_id"), r


def readable(did):
    r = call("mempalace_get_drawer", drawer_id=did)
    return bool(r) and not r.get("error") and (r.get("content") or r.get("text") or r.get("drawer_id"))


def id_in_file(did):
    con = sqlite3.connect(f"file:{palace}/chroma.sqlite3?mode=ro", uri=True)
    try:
        return con.execute("select count(*) from embeddings where embedding_id = ?", (did,)).fetchone()[0] > 0
    finally:
        con.close()


# a) add + read back (creates the palace)
d1, r1 = add("q1 first drawer kumquat %d" % time.time())
check("a: add returns an id", bool(d1), str(r1)[:120])
check("a: read back by id", readable(d1))

# b) warm per-call cost of _get_collection
ts = []
for _ in range(10):
    t = time.perf_counter(); c = s._get_collection(); ts.append(time.perf_counter() - t)
    assert c is not None
check("b: 10 warm _get_collection calls each <= 0.5 s", max(ts) <= 0.5,
      "avg %.4f s, max %.4f s" % (sum(ts) / len(ts), max(ts)))

# c) inode replacement: next save must land in the NEW file
db = os.path.join(palace, "chroma.sqlite3")
ino_before = os.stat(db).st_ino
tmp = db + ".q1copy"
shutil.copy2(db, tmp)
os.replace(tmp, db)
check("c: file was replaced (new inode)", os.stat(db).st_ino != ino_before)
d2, r2 = add("q1 after replace persimmon %d" % time.time())
check("c: save after replace succeeds", bool(d2), str(r2)[:120])
check("c: that drawer is in the NEW file", bool(d2) and id_in_file(d2))
check("c: and reads back through the tools", bool(d2) and readable(d2))

# d) external mtime change seen first by a search, then a save
os.utime(db, None)
sr = call("mempalace_search", query="kumquat", wing="q1", limit=3)
check("d: search after external touch works", "results" in sr, str(sr)[:120])
d3, r3 = add("q1 after touch tamarind %d" % time.time())
check("d: save after search reads back", bool(d3) and readable(d3) and id_in_file(d3))

# e) reconnect refuses while a mine (long operation) runs on another thread
started, release = threading.Event(), threading.Event()


def fake_mine():
    with hub_lock.long_operation():
        started.set(); release.wait(10)


th = threading.Thread(target=fake_mine); th.start(); started.wait(5)
rr = call("mempalace_reconnect")
release.set(); th.join(5)
check("e: reconnect refused during a mine on another thread",
      rr.get("success") is False and "mine is running" in str(rr.get("error")), str(rr)[:120])
rr2 = call("mempalace_reconnect")
check("e: reconnect works after the mine", rr2.get("success") is not False, str(rr2)[:120])
d4, _ = add("q1 after reconnect loquat %d" % time.time())
check("e: save after reconnect reads back", bool(d4) and readable(d4) and id_in_file(d4))

print("Q1_OK" if not fails else "Q1_FAIL %s" % fails)
