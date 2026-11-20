# G1: tool env runs a locally built chromadb 1.5.9 (bindings .so differs from the PyPI wheel's)
PY=~/.local/share/uv/tools/mempalace/bin/python
v=$($PY -I -c 'import importlib.metadata as m; print(m.version("chromadb"))')
so=$($PY -I -c 'import chromadb_rust_bindings as b, os; d=os.path.dirname(b.__file__); print(os.path.join(d, [f for f in os.listdir(d) if f.endswith(".so")][0]))')
h=$(shasum -a 256 "$so" | cut -c1-64)
echo "chromadb $v  so $h"
[ "$v" = 1.5.9 ] && [ "$h" != "26031d103917aa58eb054e60e5daa03ef8a3ffada8a932e2840fb4fef482ec72" ] && $PY -I -c 'import chromadb; c=chromadb.EphemeralClient().get_or_create_collection("g1check"); e=[[0.1]*8,[0.2]*8]; c.add(ids=["a","b"],embeddings=e,documents=["x","y"]); c.upsert(ids=["b","c"],embeddings=e,documents=["y2","z"]); c.update(ids=["a"],embeddings=[[0.3]*8],documents=["x2"]); c.delete(ids=["c"]); assert c.count()==2 and c.get(ids=["a","b"])["documents"]==["x2","y2"]' && echo G1_OK || echo G1_FAIL
