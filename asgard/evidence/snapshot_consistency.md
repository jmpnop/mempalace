# Snapshot (clone) consistency evidence — 2026-11-20

Scripts: `snapshot_snapprobe.py` (clone the real 7.2 GB palace database under a read transaction, quick_check the clone),
`snapshot_torture2.py` (external writer process vs. repeated clones; `nolock` = negative control). Intel Mac, macOS 15.8, APFS, Python sqlite 3.50.4.

## Real palace clone
```
{"journal_mode": "delete", "hot_journal_present": false, "clonefile_rc": 0, "errno": 0, "clone_s": 0.0005, "size_src": 7244337152, "size_dst": 7244337152, "clone_quick_check_s": 255.8, "clone_verdict": ["ok"]}
```

## Torture runs (summary lines)
```
{"mode": "delete", "DONE": true, "iterations": 300, "bad": 0}
{"mode": "wal", "DONE": true, "iterations": 300, "bad": 0}
negative control (no read transaction): 83 of 168 clones inconsistent; then: sqlite3.DatabaseError: database disk image is malformed
```

## Helper-process clone, immutable open, unlink after open (`snapshot_unlinkprobe.py`, ~80 MB database)
```
{"mode": "delete", "helper_s": 0.089, "sidecars_left": [], "name_gone": true, "quick_check_unlinked": "ok", "rows_in_clone": 20000, "MB_pinned_while_open": 82.2, "MB_returned_on_close": 82.0}
{"mode": "wal", "helper_s": 0.09, "sidecars_left": [], "name_gone": true, "quick_check_unlinked": "ok", "rows_in_clone": 20000, "MB_returned_on_close": 82.3}
```
## Own-descriptor tripwire (`snapshot_fdprobe.py`, real chromadb client in-process)
```
{"before": [], "chroma_open_after_write": [9, 10], "after_mempalace_close": [], "own_reader_open": [3], "after_own_reader_close": []}
```
