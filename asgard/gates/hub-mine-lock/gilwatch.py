# gilwatch.py PALACE WING LIMIT — run `mempalace mine` in-process with a 5 ms ticker thread; every tick gap
# >100 ms means some thread held the GIL that long. Record the gap and the mining thread's Python stack.
import collections, os, sys, threading, time, traceback
palace, wing, limit = sys.argv[1:4]
gaps = []; main_id = threading.get_ident(); stop = False
def tick():
    last = time.perf_counter()
    while not stop:
        time.sleep(0.005); now = time.perf_counter(); g = now - last; last = now
        if g > 0.1:
            fr = sys._current_frames().get(main_id)
            st = [f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}" for f in traceback.extract_stack(fr)[-6:]] if fr else []
            gaps.append((g, st))
threading.Thread(target=tick, daemon=True).start()
sys.argv = ["mempalace", "--palace", palace, "mine", os.environ["MINE_SRC"], "--mode", "projects",
            "--wing", wing, "--agent", "bench", "--limit", limit, "--direct"]
from mempalace.cli import main
t = time.time()
try: main()
except SystemExit: pass
stop = True
print("\nwall %.1fs, gaps>100ms: %d, total %.1fs, worst %.2fs" % (time.time()-t, len(gaps), sum(g for g,_ in gaps), max([g for g,_ in gaps] or [0])))
by = collections.defaultdict(list)
for g, st in gaps: by[" <- ".join(reversed(st[-3:]))].append(g)
for k, v in sorted(by.items(), key=lambda kv: -sum(kv[1]))[:12]:
    print("%5d x  sum %6.2fs  max %5.2fs  %s" % (len(v), sum(v), max(v), k[:230]))
