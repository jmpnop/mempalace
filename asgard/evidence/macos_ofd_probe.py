# ofd_probe.py — does macOS give OFD locks the semantics _inproc_sqlite's anchor needs?
import fcntl, os, struct, subprocess, sys, tempfile
F_OFD_SETLK, F_OFD_GETLK = fcntl.F_OFD_SETLK, fcntl.F_OFD_GETLK
# struct flock on Darwin: off_t l_start, off_t l_len, pid_t l_pid, short l_type, short l_whence
FMT = "qqihh"
def flock(t, start=0, length=1): return struct.pack(FMT, start, length, 0, t, os.SEEK_SET)
def try_lock(fd, cmd, t, start=0, length=1):
    try: fcntl.fcntl(fd, cmd, flock(t, start, length)); return True
    except OSError as e: return f"errno {e.errno}"
def getlk(fd, cmd, t, start=0, length=1):
    r = fcntl.fcntl(fd, cmd, flock(t, start, length)); return struct.unpack(FMT, r)[3]  # l_type: 2=F_UNLCK
path = tempfile.mktemp(); open(path, "wb").write(b"x" * 4096)
res = {}
a = os.open(path, os.O_RDWR); res["ofd read lock taken"] = try_lock(a, F_OFD_SETLK, fcntl.F_RDLCK, 100, 10)
c = os.open(path, os.O_RDONLY); os.close(c)  # closing ANOTHER fd of the same file in this process
b = os.open(path, os.O_RDWR)
res["OFD lock still seen after closing another fd (GETLK from a new OFD; F_UNLCK=2 means gone)"] = getlk(b, F_OFD_GETLK, fcntl.F_WRLCK, 100, 10)
res["same-process classic F_SETLK write lock conflicts with the OFD read lock"] = try_lock(b, fcntl.F_SETLK, fcntl.F_WRLCK, 100, 10)
child = subprocess.run([sys.executable, "-I", "-c", f"""
import fcntl, os, struct
fd = os.open({path!r}, os.O_RDWR)
try:
    fcntl.fcntl(fd, fcntl.F_SETLK, struct.pack('qqihh', 100, 10, 0, fcntl.F_WRLCK, 0)); print('child write lock: GRANTED')
except OSError as e: print('child write lock: blocked errno', e.errno)
"""], capture_output=True, text=True)
res["another process's write lock vs our OFD read lock"] = child.stdout.strip()
os.close(a)
res["after closing the OFD's own fd, lock released (F_UNLCK=2)"] = getlk(b, F_OFD_GETLK, fcntl.F_WRLCK, 100, 10)
os.close(b); os.unlink(path)
for k, v in res.items(): print(f"{k}: {v}")
