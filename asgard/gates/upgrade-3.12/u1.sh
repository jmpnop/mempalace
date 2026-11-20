PY=~/.local/share/uv/tools/mempalace/bin/python; SP=~/.local/share/uv/tools/mempalace/lib/python3.13/site-packages
v=$($PY -I -c 'import importlib.metadata as m; print(m.version("mempalace"), m.version("chromadb"))')
so=$(ls $SP/chromadb_rust_bindings/*.so | head -1); h=$(shasum -a 256 "$so" | cut -c1-64)
stray=$(test -e $SP/mempalace/hub_lock.py && echo yes || echo no)
hz=$(curl -s -m 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/healthz); pid=$(launchctl list | awk '/com.mempalace.hub/{print $1}')
hv=$(ps -o command= -p $pid | head -1 | cut -c1-80)
echo "versions: $v | bindings $h | stray hub_lock: $stray | hub pid $pid healthz $hz"
[ "$v" = "3.12.0 1.5.9" ] && [ "$h" = 2fedfa9949159c30dea086f8ac3c18e9c17d14a8448d8eaa98bbd2e7ffc65e20 ] && [ $stray = no ] && [ "$hz" = 200 ] && echo U1_OK || echo U1_FAIL
