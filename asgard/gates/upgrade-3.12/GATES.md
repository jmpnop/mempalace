# Gates: asgard MemPalace hub upgraded 3.10.0 -> 3.12.0 (upstream fixes replace local mempalace patches)

- [ ] U1: installed mempalace is 3.12.0, chromadb is the locally patched 1.5.9 build (bindings sha256 2fedfa99...), no stray local mempalace files (hub_lock.py), hub healthy
  CHECK: bash ./u1.sh
  EXPECT: U1_OK
- [ ] U2: on 3.12, after one cold save, four warm saves each <= 6 s and saves interleaved with searches <= 6 s
  CHECK: bash ./u2.sh
  EXPECT: U2_OK
- [ ] U3: on 3.12, a 20-file throwaway-wing mine through the hub completes, while every concurrent search answers <= 30 s, /healthz <= 2 s, and an outside add_drawer succeeds <= 120 s
  CHECK: bash ./u3.sh
  EXPECT: U3_OK
- [ ] U4: afterwards the zz-hub-probe wing is empty and repair-status reports OK
  CHECK: bash ./u4.sh
  EXPECT: U4_OK
