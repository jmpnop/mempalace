"""(1) A helper process holds a read transaction and clones db (+wal); (2) the parent folds a WAL clone into one file,
opens the clone immutable, unlinks every name, and runs quick_check on the unlinked clone; (3) disk space is returned."""
import ctypes, ctypes.util, os, sqlite3, subprocess, sys, json, time
d, mode = sys.argv[1], sys.argv[2]; assert os.path.isdir(d)
db = os.path.join(d, f"live-{mode}.sqlite3")
for s in ("", "-wal", "-shm", "-journal"):
    if os.path.exists(db + s): os.unlink(db + s)
c = sqlite3.connect(db, isolation_level=None); c.execute(f"PRAGMA journal_mode={mode}"); c.execute("PRAGMA wal_autocheckpoint=0")
c.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, pad BLOB)")
c.execute("BEGIN"); [c.execute("INSERT INTO t(pad) VALUES(?)", (os.urandom(4000),)) for _ in range(20000)]; c.execute("COMMIT")  # ~80 MB
HELPER = r'''
import ctypes,ctypes.util,os,sqlite3,sys,json
db,dst=sys.argv[1],sys.argv[2]
libc=ctypes.CDLL(ctypes.util.find_library("c"),use_errno=True); libc.clonefile.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_uint32]
c=sqlite3.connect(db,isolation_level=None); c.execute("BEGIN"); c.execute("SELECT count(*) FROM sqlite_master").fetchone()
made=[]
for s in ("","-wal"):
    if s and not os.path.exists(db+s): continue
    if libc.clonefile((db+s).encode(),(dst+s).encode(),0)!=0: print(json.dumps({"err":ctypes.get_errno()})); sys.exit(1)
    made.append(s)
c.execute("COMMIT"); print(json.dumps({"cloned":made}),flush=True)
'''
dst = os.path.join(d, f"snap-{mode}.sqlite3")
for s in ("", "-wal", "-shm"):
    if os.path.exists(dst + s): os.unlink(dst + s)
t = time.perf_counter(); h = subprocess.run([sys.executable, "-I", "-c", HELPER, db, dst], capture_output=True, text=True)
res = {"mode": mode, "helper": h.stdout.strip(), "helper_s": round(time.perf_counter() - t, 3)}
if os.path.exists(dst + "-wal"):           # fold the cloned WAL into the clone (private file), leaving one file
    k = sqlite3.connect(dst, isolation_level=None); res["wal_fold"] = k.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    k.execute("PRAGMA journal_mode=delete"); k.close()
res["sidecars_left"] = [s for s in ("-wal", "-shm", "-journal") if os.path.exists(dst + s)]
free0 = os.statvfs(d).f_bavail * os.statvfs(d).f_frsize
k = sqlite3.connect(f"file:{dst}?mode=ro&immutable=1", uri=True)
k.execute("SELECT count(*) FROM sqlite_master").fetchone()   # file opened
os.unlink(dst); res["name_gone"] = not os.path.exists(dst)
c.execute("UPDATE t SET pad=randomblob(4000)")                 # rewrite every live page: clone blocks become private
c.execute("PRAGMA wal_checkpoint(TRUNCATE)") if mode == "wal" else None
free1 = os.statvfs(d).f_bavail * os.statvfs(d).f_frsize
t = time.perf_counter(); res["quick_check_unlinked"] = k.execute("PRAGMA quick_check").fetchone()[0]; res["qc_s"] = round(time.perf_counter() - t, 2)
res["rows_in_clone"] = k.execute("SELECT count(*) FROM t").fetchone()[0]
k.close(); time.sleep(0.5)
free2 = os.statvfs(d).f_bavail * os.statvfs(d).f_frsize
res["MB_pinned_while_open"] = round((free0 - free1) / 1e6, 1); res["MB_returned_on_close"] = round((free2 - free1) / 1e6, 1)
print(json.dumps(res))
