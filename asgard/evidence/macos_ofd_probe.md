# macOS supports OFD locks (probe run 2026-11-20, macOS 15.8.1 / Darwin 24.6.0, Python 3.13.7)

Upstream `backends/_inproc_sqlite.py` enables OFD locks only on Linux and states that macOS has none, so macOS falls
back to a helper process and "keeps the cross-process lock drop". On current macOS:

- SDK `sys/fcntl.h`: `F_OFD_SETLK 90`, `F_OFD_SETLKW 91`, `F_OFD_GETLK 92`, `F_OFD_SETLKWTIMEOUT 93`
  (the full developer IDE SDK and the Command Line Tools SDK); `fcntl(2)` documents OFD locks; Python 3.13's `fcntl` exports
  `F_OFD_GETLK`, `F_OFD_SETLK`, `F_OFD_SETLKW`.
- `macos_ofd_probe.py` (Darwin `struct flock` layout `qqihh`: start, len, pid, type, whence) measured:

| Property the anchor relies on | Result |
|---|---|
| OFD read lock taken | yes |
| still held after the process closes another descriptor of the same file | yes (`F_OFD_GETLK` reports `F_RDLCK`) |
| conflicts with the same process's classic `F_SETLK` write lock | yes (`errno 35`, EAGAIN) |
| conflicts with another process's write lock | yes (`errno 35`) |
| released when its own descriptor closes | yes |

Note: upstream packs the request as `"@hhqqi"` (Linux layout); Darwin needs `"qqihh"`.
