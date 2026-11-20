"""Clone-consistency torture v2. An external writer process commits continuously into a BOUNDED database: each
transaction inserts k rows into both a and b with the same ids and deletes the oldest rows from both, so every committed
state has identical id sets in a and b. The writer uses a tiny page cache so it tries to spill uncommitted pages to the
file mid-transaction. The main process loops: read transaction -> clone db (and -wal) -> end transaction -> check the
clone: integrity_check ok; a and b equal (count and sum of ids); rollback: clone equals what the reader saw; WAL: clone
is not older than what the reader saw. One JSON line per iteration."""
import ctypes, ctypes.util, os, sqlite3, subprocess, sys, time, json, random
mode, d, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
NOLOCK = len(sys.argv) > 4 and sys.argv[4] == "nolock"
assert os.path.isdir(d), "scratch directory required"
libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
libc.clonefile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
def clone(a, b):
    if libc.clonefile(a.encode(), b.encode(), 0) != 0: raise OSError(ctypes.get_errno(), "clonefile", a)
def rm(p):
    for s in ("", "-wal", "-shm", "-journal"):
        if os.path.exists(p + s): os.unlink(p + s)
db = os.path.join(d, f"{mode}.sqlite3"); rm(db)
c = sqlite3.connect(db, isolation_level=None); c.execute(f"PRAGMA journal_mode={mode}")
c.execute("CREATE TABLE a(id INTEGER PRIMARY KEY, pad BLOB)"); c.execute("CREATE TABLE b(id INTEGER PRIMARY KEY, pad BLOB)"); c.close()
WRITER = r'''
import sqlite3,sys,os,random
c=sqlite3.connect(sys.argv[1],isolation_level=None,timeout=60); c.execute("PRAGMA cache_size=8")
nid=0
while True:
    c.execute("BEGIN IMMEDIATE")
    for _ in range(random.randint(1,60)):
        nid+=1; p=os.urandom(random.randint(200,8000))
        c.execute("INSERT INTO a VALUES(?,?)",(nid,p)); c.execute("INSERT INTO b VALUES(?,?)",(nid,p))
    lo=nid-3000
    c.execute("DELETE FROM a WHERE id<=?",(lo,)); c.execute("DELETE FROM b WHERE id<=?",(lo,))
    c.execute("COMMIT")
'''
w = subprocess.Popen([sys.executable, "-I", "-c", WRITER, db]); time.sleep(1.5)
r = sqlite3.connect(db, isolation_level=None, timeout=60)
bad = 0
try:
    for k in range(n):
        time.sleep(random.uniform(0, 0.02))
        cl = os.path.join(d, f"clone-{mode}.sqlite3"); rm(cl)
        if not NOLOCK:
            r.execute("BEGIN"); seen = r.execute("SELECT count(*), coalesce(sum(id),0), coalesce(max(id),0) FROM a").fetchone()
        t = time.perf_counter(); clone(db, cl)
        if mode == "wal" and os.path.exists(db + "-wal"): clone(db + "-wal", cl + "-wal")
        ct = time.perf_counter() - t
        if not NOLOCK: r.execute("COMMIT")
        k2 = sqlite3.connect(cl, isolation_level=None)
        ic = [x[0] for x in k2.execute("PRAGMA integrity_check").fetchall()]
        A = k2.execute("SELECT count(*), coalesce(sum(id),0), coalesce(max(id),0) FROM a").fetchone()
        B = k2.execute("SELECT count(*), coalesce(sum(id),0), coalesce(max(id),0) FROM b").fetchone()
        k2.close()
        ok = ic == ["ok"] and A == B and (NOLOCK or (A == seen if mode == "delete" else A[2] >= seen[2]))
        if NOLOCK: seen = None
        bad += not ok
        print(json.dumps({"mode": mode, "k": k, "ok": ok, "clone_ms": round(ct * 1000, 2), "seen": seen, "A": A, "B": B,
                          "ic": ic[:2], "db_mb": round(os.path.getsize(db) / 1e6, 1)}), flush=True)
finally:
    w.kill(); w.wait()
print(json.dumps({"mode": mode, "DONE": True, "iterations": n, "bad": bad, "nolock": NOLOCK}), flush=True)
