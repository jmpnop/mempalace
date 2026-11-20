# g5_unit.py — hub_lock semantics against the verbatim _RWLock class from the installed http.py.
import re, threading, time, os
M = os.path.expanduser("~/.local/share/uv/tools/mempalace/lib/python3.13/site-packages/mempalace")
src = open(M + "/mcp_server/http.py").read()
cls = re.search(r"\nclass _RWLock:\n.*?(?=\n\n\n|\n_HTTP_REQUEST_LOCK)", src, re.S).group(0)
ns = {"threading": threading}; exec(cls, ns); RW = ns["_RWLock"]
from mempalace import hub_lock
fails = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name); cond or fails.append(name)
def try_read(rw, timeout):
    done = threading.Event()
    def f(): rw.acquire_read(); done.set(); rw.release_read()
    threading.Thread(target=f, daemon=True).start(); return done.wait(timeout)

rw = RW(); hub_lock.install(rw)
# a) reads run alongside a long operation (outside its write batches)
step = threading.Event(); go = threading.Event(); log = []
def mine():
    with hub_lock.long_operation():
        step.set(); go.wait(10)
        with hub_lock.exclusive():
            log.append("batch-start"); time.sleep(0.6); log.append("batch-end")
        log.append("after-batch")
t = threading.Thread(target=mine); t.start(); step.wait(5)
check("a: read concurrent with long operation", try_read(rw, 1.0))
# b) a read issued during the write batch waits until the batch ends
go.set(); time.sleep(0.2)
t0 = time.time(); got = try_read(rw, 3.0); waited = time.time() - t0
check("b: read waits for the write batch (%.2fs)" % waited, got and waited >= 0.3 and "batch-end" in log)
t.join(5); check("b: long operation finished", not t.is_alive())
# c) an outside writer waiting during the shared phase runs at the next batch boundary; no deadlock
rw2 = RW(); hub_lock.install(rw2); order = []; ready = threading.Event()
def mine2():
    with hub_lock.long_operation():
        ready.set(); time.sleep(0.5)
        for i in range(3):
            with hub_lock.exclusive(): order.append("mine-batch-%d" % i); time.sleep(0.05)
def outside_writer():
    ready.wait(5); time.sleep(0.1)
    with rw2: order.append("outside-write")
a = threading.Thread(target=mine2); b = threading.Thread(target=outside_writer); a.start(); b.start()
a.join(10); b.join(10)
check("c: no deadlock", not a.is_alive() and not b.is_alive())
check("c: outside write ran, not during a mine batch: %s" % order, "outside-write" in order and len(order) == 4)
# d) outside a long operation exclusive() is a no-op even while the thread holds the coarse lock
rw3 = RW(); hub_lock.install(rw3); ok = threading.Event()
def coarse():
    with rw3:
        with hub_lock.exclusive(): ok.set()
c = threading.Thread(target=coarse, daemon=True); c.start(); c.join(3)
check("d: exclusive() no-op under coarse lock (no self-deadlock)", ok.is_set())
# e) after a long operation (also one that raises) the lock is fully released
def boom():
    with hub_lock.long_operation():
        with hub_lock.exclusive(): raise RuntimeError("x")
try: boom()
except RuntimeError: pass
got_w = threading.Event()
def w():
    with rw3: got_w.set()
threading.Thread(target=w, daemon=True).start()
check("e: lock released after exception (writer acquires)", got_w.wait(2) and getattr(hub_lock._tls, "mode", None) is None)
# f) long_operation_active() is true only inside a long operation (any thread), false after, incl. after raising
rw4 = RW(); hub_lock.install(rw4); seen = {}
def lo():
    with hub_lock.long_operation(): seen["inner"] = hub_lock.long_operation_active(); time.sleep(0.3)
th = threading.Thread(target=lo); th.start(); time.sleep(0.1); seen["other_thread"] = hub_lock.long_operation_active(); th.join(3)
try:
    with hub_lock.long_operation(): raise ValueError("y")
except ValueError: pass
check("f: long_operation_active %s, after=%s" % (seen, hub_lock.long_operation_active()),
      seen.get("inner") and seen.get("other_thread") and not hub_lock.long_operation_active())
print("G5_OK" if not fails else "G5_FAIL %s" % fails)
