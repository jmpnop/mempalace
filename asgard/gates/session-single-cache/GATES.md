# Gates: single client cache (DESIGN.md, Design A)

Negative control (recorded before the patch, live hub on the mine-lock code): five saves 38.6 42.1 23.5 31.9 47.1 s,
5 client opens -> S1 latency gate FAILS on the old code.

- [ ] Q1: in-process on a throwaway palace (never the live one): add/read-back works; 10 warm `_get_collection` calls each
      <= 0.5 s; after `os.replace` of chroma.sqlite3 with a copy (new inode) the next save lands in the NEW file and reads
      back; `touch -m` -> search -> save reads back; reconnect refuses while a mine runs on another thread
  CHECK: bash ./q1.sh
  EXPECT: Q1_OK
- [ ] Q2: live hub: five consecutive saves each <= 6 s client-side and <= 1 client open across them
  CHECK: bash ./q2.sh
  EXPECT: Q2_OK
- [ ] Q3: live hub: save -> search -> save interleaved three times, every save <= 6 s
  CHECK: bash ./q3.sh
  EXPECT: Q3_OK
- [ ] Q4: live hub, the ordering the review found: an external mtime change (touch -m) seen first by a SEARCH -> the log
      shows a client rebuild -> the next SAVE reads back by id
  CHECK: bash ./q4.sh
  EXPECT: Q4_OK
- [ ] Q5: live hub: a 5-file mine through the hub into zz-hub-probe -> save -> read back by id and found by search;
      repair-status OK; probe wing cleaned afterwards
  CHECK: bash ./q5.sh
  EXPECT: Q5_OK
- [ ] Q6: mine-lock regression: G5 unit gate and G6 live gate pass on the patched hub
  CHECK: bash ./q6.sh
  EXPECT: Q6_OK
