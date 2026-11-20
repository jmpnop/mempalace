# Gates: readers run during a mine's post-write integrity check (commit ef89c2cb on v3.12.0)

- [ ] P1: the new unit tests (TestRWLockWriteDowngraded, tests/test_mine_shared_phase.py) and the related upstream
      suites pass (baseline on v3.12.0: 132 passed, 6 skipped)
  CHECK: cd ../../.. && .venv/bin/python -m pytest tests/test_mcp_http_transport.py tests/test_hub_reconnect_during_mine.py tests/test_mine_lock_lifecycle.py tests/test_palace_locks.py tests/test_mcp_http_call_timing.py tests/test_mine_shared_phase.py -q -p no:cacheprovider
  EXPECT: 140 passed, 6 skipped
- [ ] P1n: negative control — the new tests fail on unmodified v3.12.0 (measured: 6 failed, 2 passed; the 2 cover
      deliberately unchanged behaviour)
- [ ] D1: the installed package is byte-identical to this fork commit's mempalace/, chromadb is the patched build,
      the hub is healthy
  CHECK: bash ./d1.sh
  EXPECT: D1_OK
- [ ] P2: live hub, 20-file mine through the hub: the mine completes, and no search's lock wait during the mine
      exceeds 60 s (from the hub's own `CALL mempalace_search read wait=…ms` lines)
  CHECK: MINE_SRC=<git checkout> bash ./p2.sh
  EXPECT: P2_OK
- [ ] P3: afterwards the probe wing is empty and repair-status reports OK
  CHECK: bash ./p3.sh
  EXPECT: P3_OK
