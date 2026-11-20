# Gates: chromadb 1.5.9 write bindings release the GIL; the MemPalace hub stays responsive during a mine

- [ ] G1: The mempalace tool env imports a locally built chromadb 1.5.9 whose bindings .so differs from the PyPI build
  CHECK: bash ./g1.sh
  EXPECT: G1_OK
- [ ] G2: A 60-file throwaway mine with the patched chromadb shows no GIL hold over 250 ms inside chromadb _upsert/_add (5 ms watchdog)
  CHECK: bash ./g2.sh
  EXPECT: G2_OK
- [ ] G3: The restarted hub answers /healthz within 2 s on every probe for 3 minutes while a throwaway-wing mine writes through it, and the mine files drawers
  CHECK: bash ./g3.sh
  EXPECT: G3_OK
- [ ] G4: Through the mempalace-mcp stdio proxy: initialize, add a probe drawer, find it by search, delete it; palace repair-status OK
  CHECK: bash ./g4.sh
  EXPECT: G4_OK
