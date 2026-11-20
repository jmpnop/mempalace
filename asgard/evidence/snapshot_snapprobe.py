"""Clone chroma.sqlite3 with clonefile(2) while holding a SQLite read transaction; then quick_check the clone."""
import ctypes, ctypes.util, os, sqlite3, sys, time, json
src, dst = sys.argv[1], sys.argv[2]
assert src != dst
libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
libc.clonefile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
out = {}
c = sqlite3.connect(f"file:{src}?mode=ro", uri=True, isolation_level=None)
c.execute("BEGIN"); c.execute("SELECT count(*) FROM sqlite_master").fetchone()   # SHARED held from here
out["journal_mode"] = c.execute("PRAGMA journal_mode").fetchone()[0]
out["hot_journal_present"] = os.path.exists(src + "-journal")
t = time.perf_counter()
if os.path.exists(dst): os.unlink(dst)
rc = libc.clonefile(src.encode(), dst.encode(), 0)
out["clonefile_rc"] = rc; out["errno"] = ctypes.get_errno(); out["clone_s"] = round(time.perf_counter() - t, 4)
c.execute("COMMIT"); c.close()                                                    # SHARED released
out["size_src"], out["size_dst"] = os.path.getsize(src), os.path.getsize(dst)
k = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
t = time.perf_counter(); rows = k.execute("PRAGMA quick_check").fetchall()
out["clone_quick_check_s"] = round(time.perf_counter() - t, 1); out["clone_verdict"] = [r[0] for r in rows][:5]
print(json.dumps(out), flush=True)
