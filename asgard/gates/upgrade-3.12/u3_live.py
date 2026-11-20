# g6_live.py — live hub: throwaway-wing mine through the hub while other calls run alongside it.
import json, os, re, shutil, subprocess, threading, time, urllib.request
D = os.path.dirname(os.path.abspath(__file__)); MP = os.path.join(D, "mp_call.py"); SRC = D + "/u3src"
shutil.rmtree(SRC, ignore_errors=True); os.makedirs(SRC)
files = subprocess.run(["git", "ls-files"], cwd=os.environ["MINE_SRC"], capture_output=True, text=True).stdout.split()
files = [f for f in files if f.endswith((".md", ".py"))][:20]
for f in files:
    os.makedirs(os.path.dirname(os.path.join(SRC, f)) or SRC, exist_ok=True)
    shutil.copy(os.path.join(os.environ["MINE_SRC"], f), os.path.join(SRC, f))
def call(tool, args, timeout=300):
    t = time.time()
    try:
        r = subprocess.run(["python3", "-I", MP, tool, json.dumps(args)], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return time.time() - t, ""
    return time.time() - t, r.stdout
log = open(D + "/u3-mine.log", "w")
t0 = time.time()
mine = subprocess.Popen(["timeout", "1800", os.path.expanduser("~/.local/bin/mempalace"), "mine", SRC, "--mode", "projects",
                         "--wing", "zz-hub-probe", "--agent", "asgard-gates"], stdout=log, stderr=subprocess.STDOUT)
hz = []; stop = False
def health():
    while not stop:
        t = time.time()
        try: urllib.request.urlopen("http://127.0.0.1:8765/healthz", timeout=30).read(); hz.append(time.time() - t)
        except Exception: hz.append(99.0)
        time.sleep(2)
threading.Thread(target=health, daemon=True).start()
searches = []; add = None; probe_id = None
time.sleep(20)
while mine.poll() is None:
    if add is None and time.time() - t0 > 40:
        dt, out = call("mempalace_add_drawer", {"wing": "zz-hub-probe", "room": "probe", "added_by": "asgard-gates",
                                                "content": "g6 outside write during mine %d" % time.time()})
        m = re.search(r'"drawer_id": "([^"]+)"', out); probe_id = m and m.group(1)
        add = (dt, '"success": true' in out, mine.poll() is None); print("add_drawer %.1fs ok=%s during_mine=%s" % add, flush=True)
        continue
    dt, out = call("mempalace_search", {"query": "hub probe outside write", "wing": "zz-hub-probe", "limit": 1})
    during = mine.poll() is None
    searches.append((dt, '"results"' in out, during)); print("search %.1fs ok=%s during_mine=%s" % searches[-1], flush=True)
    time.sleep(8)
rc = mine.wait(); stop = True; log.close()
mlog = open(D + "/u3-mine.log", errors="replace").read()
filed = re.search(r"Drawers filed:\s+(\d+)", mlog)
if probe_id:
    call("mempalace_delete_drawer", {"drawer_id": probe_id})
dur = [s for s in searches if s[2]]
print("mine rc=%s %.0fs filed=%s | searches during mine=%d worst=%.1fs all_ok=%s | healthz n=%d worst=%.1fs | add=%s"
      % (rc, time.time() - t0, filed and filed.group(1), len(dur), max([s[0] for s in dur] or [0]),
         all(s[1] for s in dur), len(hz), max(hz or [0]), add))
ok = (rc == 0 and filed and int(filed.group(1)) > 0 and len(dur) >= 3 and all(s[1] and s[0] <= 30 for s in dur)
      and hz and max(hz) <= 2 and add and add[1] and add[2] and add[0] <= 120)
print("U3_OK" if ok else "U3_FAIL")
