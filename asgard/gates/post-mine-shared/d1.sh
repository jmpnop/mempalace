cd "$(dirname "$0")"; R=$(cd ../../.. && pwd); SP=~/.local/share/uv/tools/mempalace/lib/python3.13/site-packages
d=$(diff -rq -x __pycache__ "$R/mempalace" "$SP/mempalace" | wc -l | tr -d ' ')
h=$(shasum -a 256 $SP/chromadb_rust_bindings/*.so | cut -c1-64); hz=$(curl -s -m 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/healthz)
c=$(cd "$R" && git rev-parse --short HEAD); dirty=$(cd "$R" && git status --porcelain -- mempalace | wc -l | tr -d ' ')
echo "fork commit $c (uncommitted mempalace changes: $dirty) | installed vs fork: $d differing files | bindings $h | healthz $hz"
[ "$d" = 0 ] && [ "$dirty" = 0 ] && [ "$h" = 2fedfa9949159c30dea086f8ac3c18e9c17d14a8448d8eaa98bbd2e7ffc65e20 ] && [ "$hz" = 200 ] && echo D1_OK || echo D1_FAIL
