# 04 — Upgrade of the asgard hub from MemPalace 3.10.0 to 3.12.0

**Date:** 2026-11-20. **Decision:** use the latest release (3.12.0, 2026-11-12, also latest on PyPI) instead of
carrying local 3.10.0 patches, because 3.12.0 already contains upstream fixes for both MemPalace-level problems
([02](02-hub-mine-lock-3.10.md), [03](03-slow-saves-3.10.md)). The local chromadb rebuild ([01](01-chromadb-writes-hold-the-gil.md))
is still required and was re-applied.
**Gates:** [`../gates/upgrade-3.12/`](../gates/upgrade-3.12/) (`GATES.md`, `u1.sh`–`u4.sh`, `u3_live.py`).

## What 3.12.0 changes for these problems (read in the v3.12.0 tree)
- **Mine vs. the hub lock:** `mcp_server/http._http_dispatch` routes `mempalace_mine` through `_HTTP_YIELDING_TOOLS`:
  it takes `_HTTP_MINE_LOCK` and the exclusive `_HTTP_REQUEST_LOCK`, and `palace.mine_yield_hook(_HTTP_REQUEST_LOCK.yield_write)`
  periodically yields the write lock to waiting calls.
- **Slow saves:** `mcp_server/_session._restamp_palace_db()` re-baselines the session's freshness stat after its own
  writes; `_drop_session_client_for_system_reset()` is registered with
  `backends.chroma.register_before_system_cache_reset` so the session client is closed before the shared chromadb
  System is dropped (the cross-cache gap found in the review of the rejected design in 03).
- **Per-call timing in the hub log:** `CALL` / `SLOW CALL <tool> <read|write> wait=… run=…` lines.
- Release notes that matter here: the next mine re-reads every source once (drawer normalization version 3);
  hallways move from `hallways.json` into `hallways.sqlite3` on the first hallway write (one-time import); wing names
  are normalized (`zz-hub-probe` is filed as `zz_hub_probe`); every install sharing a palace must be upgraded
  together — this palace has no mesh peers (`mempalace_mesh_peers`: `"peers": []`).

## Procedure (as executed)
1. Rollback archive of the installed, patched 3.10.0 package + chromadb (`rollback-installed-3.10.0-patched.tgz`,
   kept locally, not in this repo).
2. Confirmed no mine was running; stopped the hub with `launchctl bootout gui/$UID/com.mempalace.hub` (KeepAlive
   would otherwise restart it onto a half-replaced package).
3. `uv tool install 'mempalace==3.12.0' --python 3.13 --reinstall`.
   *Mistake made and corrected:* the first attempt carried `--with 'pikepdf<10'` over from an unrelated ocrmypdf
   install and added pikepdf/pillow/wrapt to the tool env; the `--reinstall` without it removed them (verified).
4. The reinstall replaced the patched chromadb with PyPI's build (bindings sha256 `26031d10…`); re-installed the
   patched wheel (`2fedfa99…`). *Mistake made and corrected:* the wheel had been stored under a descriptive name
   that is not a valid wheel filename; stored again as `wheels/{patched,official}/chromadb-1.5.9-cp39-abi3-macosx_10_12_x86_64.whl`.
5. Removed the leftover `mempalace/hub_lock.py` from patch 02 (not part of any package; byte-compared to the patch
   copy before removal).
6. `launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.mempalace.hub.plist`; healthy after 14 s.

## Results
| Gate | Result |
|---|---|
| U1 installed 3.12.0 + patched chromadb, no stray local files, hub healthy | OK (negative control before the upgrade: FAIL — 3.10.0, `hub_lock.py` present) |
| U2 saves | cold 20.1 s; warm **3.5 3.5 3.6 3.5 3.5 s** (21–47 s on unpatched 3.10.0) → OK |
| U3 20-file mine through the hub with concurrent calls | **FAIL** — mine rc 0, 834 s, 216 drawers; `/healthz` worst 1.0 s (402 probes); outside `add_drawer` 5.0 s; but searches: 76.6 / 24.1 / 19.6 / 17.8 s, then two timed out at 300 s |
| U4 cleanup + `repair-status` | OK — probe wing empty (checked under both spellings), drawers/closets OK |

### Why U3 failed (from 3.12's own `CALL` timing)
- During the mine's write phase the yield works: searches waited 0.5–3.1 s for the lock (one waited 59.6 s at the
  start).
- After the write phase the mine ran the one-time hallways import (37 261 records) and its end-of-mine validation
  without yielding: `SLOW CALL mempalace_search read wait=578198ms` (9.6 min) and `wait=270158ms`.
  `SLOW CALL mempalace_mine write wait=0ms run=772502ms`.
- Searches themselves now run 13–27 s (one 63 s) once they hold the lock, versus ~6 s on 3.10.0.

Both remaining problems are tracked in [05](05-open-issues.md).
