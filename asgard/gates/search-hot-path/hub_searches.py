"""hub_searches.py baseline|verify OUT_DIR — run the six profiled searches through the live hub, the way MCP clients
do (the mempalace-mcp stdio proxy), and record each search's drawer ids in rank order and its client-side time.

baseline: writes OUT_DIR/baseline.json.
verify:   writes OUT_DIR/after.json and compares with baseline.json: every search must return the same drawer ids in
          the same order. Prints SEARCH_RESULTS_IDENTICAL when they all match.
"""
import json, os, subprocess, sys, time

QUERIES = [
    {"query": "mine lock yield during integrity check", "wing": "mempalace", "limit": 5},
    {"query": "chromadb write bindings hold the GIL", "wing": "mempalace", "limit": 5},
    {"query": "Rust port of the bottleneck", "limit": 5},
    {"query": "slow MCP saves session client rebuild", "limit": 5},
    {"query": "hub restart launchd", "wing": "mempalace", "limit": 5},
    {"query": "corpus markdown conversion pandoc", "limit": 5},
]


def run_all():
    p = subprocess.Popen([os.path.expanduser("~/.local/bin/mempalace-mcp")], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)

    def rpc(i, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if i is not None:
            msg["id"] = i
        if params is not None:
            msg["params"] = params
        p.stdin.write(json.dumps(msg) + "\n"); p.stdin.flush()
        if i is None:
            return None
        return json.loads(p.stdout.readline())

    rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "gate", "version": "1"}})
    rpc(None, "notifications/initialized")
    out = []
    for n, args in enumerate(QUERIES, start=2):
        t = time.perf_counter()
        r = rpc(n, "tools/call", {"name": "mempalace_search", "arguments": args})
        dt = time.perf_counter() - t
        txt = "".join(c.get("text", "") for c in r.get("result", {}).get("content", []))
        body = json.loads(txt)
        out.append({"args": args, "seconds": round(dt, 2), "ids": [h["drawer_id"] for h in body.get("results", [])]})
    p.stdin.close(); p.wait(10)
    return out


mode, d = sys.argv[1], sys.argv[2]
os.makedirs(d, exist_ok=True)
res = run_all()
for r in res:
    print(f"{r['seconds']:7.2f}s  {r['args']['query']!r}  {len(r['ids'])} ids")
if mode == "baseline":
    json.dump(res, open(os.path.join(d, "baseline.json"), "w"), indent=1)
    print("BASELINE_RECORDED")
else:
    json.dump(res, open(os.path.join(d, "after.json"), "w"), indent=1)
    base = json.load(open(os.path.join(d, "baseline.json")))
    bad = [b["args"]["query"] for b, a in zip(base, res) if b["ids"] != a["ids"]]
    print("total before %.1fs after %.1fs" % (sum(b["seconds"] for b in base), sum(a["seconds"] for a in res)))
    print("DIFFERENT: %s" % bad if bad else "SEARCH_RESULTS_IDENTICAL")
