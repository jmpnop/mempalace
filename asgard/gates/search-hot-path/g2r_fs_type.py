"""G2r: the real (unmocked) _fs_type on this machine.

"apfs" for the given palace directory and for the system temp dir; "hfs" for a mounted
HFS+ volume when one exists (otherwise a printed note); "" for a missing path; and the
plain ``statfs`` symbol alone returns an empty name here (the reason for
``statfs$INODE64``), so the APFS answers prove the 64-bit-inode symbol was used.
"""

import ctypes
import os
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
import mempalace  # noqa: E402
from mempalace.backends.chroma import _fs_type  # noqa: E402

assert os.path.realpath(mempalace.__file__).startswith(REPO + os.sep), mempalace.__file__
ok = True


def check(label, got, want):
    global ok
    print(f"{label}: {got!r} (want {want!r})")
    ok = ok and got == want


palace = sys.argv[1]
check("palace clone", _fs_type(palace), "apfs")
check("temp dir", _fs_type(tempfile.gettempdir()), "apfs")
check("missing path", _fs_type(os.path.join(palace, "no-such-dir", "x")), "")

libc = ctypes.CDLL(None)
plain = getattr(libc, "statfs", None)
if plain is not None and hasattr(libc, "statfs$INODE64"):
    buf = ctypes.create_string_buffer(2168)
    plain(os.fsencode(palace), buf)
    name = buf.raw[72:88].split(b"\0", 1)[0]
    print(f"plain statfs symbol reads {name!r} at offset 72 (the legacy ABI; expected not b'apfs')")
    ok = ok and name != b"apfs"

mounts = subprocess.run(["mount"], capture_output=True, text=True).stdout.splitlines()
hfs = [line.split(" on ", 1)[1].split(" (", 1)[0] for line in mounts if "(hfs" in line]
if hfs:
    check(f"HFS+ volume {hfs[0]!r}", _fs_type(hfs[0]), "hfs")
else:
    print("note: no HFS+ volume mounted; the hfs case is skipped")

print("G2R_FS_TYPE_OK" if ok else "G2R_FAILED")
sys.exit(0 if ok else 1)
