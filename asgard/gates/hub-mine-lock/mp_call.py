# mp_call.py — drive mempalace-mcp (the same stdio proxy MCP clients use) for one tools/call.
import json, subprocess, sys, time
tool, args = sys.argv[1], json.loads(sys.argv[2])
p = subprocess.Popen([__import__("os").path.expanduser("~/.local/bin/mempalace-mcp")], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
def rpc(i, method, params=None):
    msg = {"jsonrpc": "2.0", "method": method}
    if i is not None: msg["id"] = i
    if params is not None: msg["params"] = params
    p.stdin.write(json.dumps(msg) + "\n"); p.stdin.flush()
    if i is None: return None
    t = time.time(); line = p.stdout.readline()
    return time.time() - t, json.loads(line)
dt, r = rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "probe", "version": "1"}})
print("initialize %.1fs ok=%s" % (dt, "result" in r))
rpc(None, "notifications/initialized")
dt, r = rpc(2, "tools/call", {"name": tool, "arguments": args})
txt = "".join(c.get("text", "") for c in r.get("result", {}).get("content", []))
print("%s %.1fs -> %s" % (tool, dt, (txt or json.dumps(r.get("error")))[:600]))
p.stdin.close(); p.wait(10)
