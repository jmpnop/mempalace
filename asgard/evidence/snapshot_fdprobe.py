"""Can a process prove it holds no descriptor on a database? Enumerate own fds, match (st_dev, st_ino) against the
db and its sidecars; open a real chromadb client, write, close it through mempalace's own close path; re-check."""
import os, sys, json, gc, shutil, sqlite3
d = sys.argv[1]; assert os.path.isdir(d)
pal = os.path.join(d, "palace"); shutil.rmtree(pal, ignore_errors=True); os.makedirs(pal)
db = os.path.join(pal, "chroma.sqlite3")
def holders():
    ids = set()
    for s in ("", "-wal", "-journal", "-shm"):
        try: st = os.stat(db + s); ids.add((st.st_dev, st.st_ino))
        except FileNotFoundError: pass
    out = []
    for name in os.listdir("/dev/fd"):
        try: fd = int(name); st = os.fstat(fd)
        except (ValueError, OSError): continue
        if (st.st_dev, st.st_ino) in ids: out.append(fd)
    return out
from mempalace.backends.chroma import ChromaBackend
import mempalace.backends.chroma as ch
from mempalace.repair import _close_chroma_handles
res = {"before": holders()}
be = ch._DEFAULT_BACKEND if hasattr(ch, "_DEFAULT_BACKEND") else ChromaBackend()
col = be.get_collection(pal, "mempalace_drawers", create=True)
col.upsert(ids=["a", "b"], documents=["alpha text", "beta text"], metadatas=[{"wing": "w", "room": "r"}] * 2)
res["chroma_open_after_write"] = holders()
_close_chroma_handles(pal, backend=be)
res["after_mempalace_close"] = holders()
c = sqlite3.connect(db); res["own_reader_open"] = holders(); c.close()
res["after_own_reader_close"] = holders()
print(json.dumps(res))
