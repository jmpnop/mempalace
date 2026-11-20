# Design: MCP session palace-collection access (single client cache)

## Decision Date
2026-11-20 (asgard). Status: PROPOSED — not implemented. Stacks on `../2026-11-20-hub-mine-lock/`.

## What We're Building
The MCP hub's tools (add/get/delete drawer, diary, search helpers, …) obtain the palace's drawers collection through
`mcp_server/_session._get_collection()`. Today the Chroma branch of that function keeps its OWN chromadb client cache
(`_get_client` → `ChromaBackend.make_client`, freshness baseline `_palace_db_inode/_palace_db_mtime`) next to the
storage backend's cache (`ChromaBackend._client`, used by search via `palace.get_collection`). The session cache never
re-baselines after the hub's own writes, so every save rebuilds the client (9.5 s, mostly
`quarantine_invalid_hnsw_metadata`) and reloads the collection (7.3 s); the upsert itself is 0.06 s (measured,
client-side save 21–47 s). A first fix (process-global "own stat" shared by both caches) failed a 3-agent POSD
adversarial review: with two caches, one cache's rebuild masks an external change from the other, leaving two
chromadb Systems persisting divergent HNSW to one palace (#2002 data-loss class). Requirement from that review: ONE
client cache.

### Phase 1 — requirements (extracted; correct me if wrong)
1. **Does:** give an MCP tool call a usable, current collection handle for the configured palace.
2. **Callers:** every MCP tool in `tools_write.py`, `tools_read.py`, `tools_diary.py`, plus `http.py`, `protocol.py`,
   `palace_graph.py`, `layers.py`, `palace/backend.py` (28 call sites of `_get_collection(`; audited 2026-11-20).
   `_get_client()` is called only inside `_session._get_collection`; `_client_cache` is otherwise touched only by
   `_guards._force_chroma_cache_reset` and `tools_diary.tool_reconnect`.
3. **Receives:** `create` flag (+ implicit config: palace path, collection name, backend, read-only mode).
   **Returns:** a collection object or `None` + `_collection_open_error` describing why.
4. **Hard decisions:** where client lifetime and freshness live; how a drain (external change, post-mine
   `_close_chroma_handles`, reconnect) reaches every holder of a collection; keeping the hub-mine-lock guarantees.
5. **Hide from callers:** client/System lifetime, freshness detection, HNSW open-time checks, embedding-function
   resolution, create-vs-open split (#1262), thread pinning (#974/#965).
6. **Known edge cases:** missing DB without `create`; backend mismatch; external writer / repair / inode replacement
   (#2002); a mine running on another thread (no reset then); read-only operator mode; a drain invalidating
   previously returned collections ("Callers must reacquire them through get_collection").

## Design Considered: A — "One Store, Re-ask Every Call"
### Interface
```python
# mcp_server/_session.py  (signature unchanged for all 28 callers)
def _get_collection(create: bool = False) -> Collection | None: ...
#   every backend, every call: palace.get_collection(palace_path, collection_name, create=..., backend=..., read_only=...)
#   no session-level client or collection cache; one retry after backend.reset_palace(...) on a stale-handle error

# backends/chroma.py  (one new method on the existing deep module; get_collection/close_palace unchanged)
class ChromaBackend:
    def get_collection(self, palace, collection_name, create=False, options=None) -> ChromaCollection: ...
    def close_palace(self, palace) -> None: ...
    def reset_palace(self, palace) -> None: ...   # close + drop chromadb's shared System cache for a fresh re-read

# removed: _session._get_client, _client_cache, _palace_db_inode/_palace_db_mtime, the Chroma-only branch
# simplified: _guards._force_chroma_cache_reset and tools_diary.tool_reconnect -> backend.reset_palace(...) + overview/
#             capacity cache invalidation; both still refuse/skip while a mine runs on another thread (hub_lock)
```
### Depth Score
9/10 — interface simplicity 2, implementation power 2, information hiding 2, error absorption 1, generality 2.
### Assessment
Deletes the second cache instead of coordinating two; every tool gets its collection from the one module that
already owns freshness (`_client` + upstream `_restamp`). Per-call cost is a warm `client.get_collection` +
`_pin_hnsw_threads` + `_restamp` (not yet measured — see Remaining Risks).

## Design Considered: B — "Session Handle + Backend Generation Token"
### Interface
```python
# backends/chroma.py
class ChromaBackend:
    def palace_generation(self, palace) -> int: ...   # bumped on every drain / close / client rebuild of that palace

# mcp_server/_session.py
_collection_cache: tuple[Collection, int] | None     # (handle, generation it was acquired under)
def _get_collection(create: bool = False) -> Collection | None: ...
#   if cached and backend.palace_generation(path) == cached_gen: return cached handle
#   else: palace.get_collection(...) and remember the new generation
```
### Depth Score
6/10 — interface simplicity 1, implementation power 2, information hiding 1, error absorption 1, generality 1.
### Assessment
Also a single client (no divergent Systems) and zero per-call reacquire cost, but it exports the client lifecycle
as a token that every caller-side cache must check before use — a conjoined protocol (acquire, remember generation,
compare) that a future caller holding a handle across calls will get wrong.

## Phase 5 — 18-principle evaluation
| # | Principle | A | A | B | B |
|---|-----------|---|---|---|---|
| 1 | Complexity minimization | removes a cache, ~150 lines, two local guards | STRONG | removes the client cache but adds a token protocol | NEUTRAL |
| 2 | Symptom awareness | one place to change freshness rules | STRONG | generation rules span backend + session | NEUTRAL |
| 3 | Cause elimination | no cross-module freshness dependency | STRONG | new dependency on generation semantics | WEAK |
| 4 | Deep modules | session becomes a thin, honest caller of a deep backend | STRONG | backend deeper, session holds lifecycle state | NEUTRAL |
| 5 | Information hiding | client/System lifetime fully inside ChromaBackend | STRONG | lifetime leaks as an int | WEAK |
| 6 | General-purpose | same path for every backend (already true for non-chroma) | STRONG | token is chroma-specific unless added to BaseBackend | NEUTRAL |
| 7 | Different layer, different abstraction | session adds error reporting/retry/read-only policy over the backend | NEUTRAL | same | NEUTRAL |
| 8 | Pull complexity downward | freshness + reset pulled into the backend | STRONG | caller must validate | WEAK |
| 9 | Composition | client and its freshness check together | STRONG | split across modules | WEAK |
| 10 | Define errors out of existence | no second System can exist; stale-handle class mostly gone | STRONG | stale only if a caller skips the check | NEUTRAL |
| 11 | Design-it-twice | compared against B | N/A | compared against A | N/A |
| 12 | Comments describe the non-obvious | needs one comment: "re-ask every call; do not cache handles" | STRONG | needs a contract comment at every holder | WEAK |
| 13 | Comments-first | signatures unchanged; easy to state | STRONG | generation semantics hard to state precisely | NEUTRAL |
| 14 | Naming precision | `reset_palace` = close + fresh re-read | STRONG | "generation" is vague (of what?) | NEUTRAL |
| 15 | Consistency | matches the non-chroma branch and miners/search path | STRONG | new pattern used nowhere else | WEAK |
| 16 | Obviousness | reader sees one call | STRONG | reader must trace token bumps | WEAK |
| 17 | Strategic vs tactical | strategic: removes the root cause; port-friendly seam | STRONG | tactical speed win | NEUTRAL |
| 18 | No temporal decomposition | structured by what owns the palace handle | STRONG | acquire/remember/compare is an execution-order protocol | WEAK |

Counts — A: STRONG 16, NEUTRAL 1, WEAK 0 (+1 N/A). B: STRONG 0, NEUTRAL 9, WEAK 8 (+1 N/A).

## Chosen Design: A — "One Store, Re-ask Every Call"
### Why
The data-loss risk and the latency bug have one cause: two independent owners of a chromadb client for one palace.
Design A removes the second owner, so there is exactly one System per palace in the hub, and its freshness is
maintained by code upstream already wrote for this purpose (`_client` + `_restamp`). Every tool keeps its call
signature, and the Chroma branch stops "bypassing that abstraction" (upstream's own words in `_session.py`).
### Tradeoffs Accepted
| Dimension | Recommended (A) | Rejected (B) |
|-----------|-----------------|--------------|
| Caller simplicity | unchanged signature, no state | caller-side cache + token check |
| Implementation complexity | net deletion; one new backend method | new counter + bump sites in backend |
| Extensibility | any backend works through palace.get_collection | token must be added per backend |
| Error handling burden | one retry in the session | retry + token mismatch path |
| Testability | backend is the single unit to test (stat source injectable there) | two units + their contract |
| Naming clarity | `get_collection`, `close_palace`, `reset_palace` | `palace_generation` needs explanation |
| Per-call cost | warm reacquire each call (unmeasured) | ~0 when generation unchanged |
### Red Flags Addressed
- Duplicate modules / information leakage (two caches, divergent freshness rules): **eliminated** in A.
- Conjoined methods (acquire → remember → compare): **present in B, absent in A**.
- Pass-through methods: A's `_get_collection` is not a pass-through (read-only policy, error reporting, retry).
- Temporal decomposition: **present in B**, absent in A.
- Special-case-heavy code (chroma-only branch with its own EF/metadata/pinning copies): **eliminated** in A.
- Shallow modules, overexposure, generic containers, config proliferation, comment repetition: not present in A.
### Remaining Risks
1. **Per-call reacquire cost** (warm `client.get_collection` + `_pin_hnsw_threads` + `_restamp` + cached identity
   check) is unmeasured. Mitigation: gate it; if it is material, memoize the collection *inside* ChromaBackend keyed to
   the client object and cleared by `_drain_clients`/`close_palace` — keeping the optimization in the deep module.
2. **Handles held across a drain inside one tool call** (a concurrent search detects an external change mid-call):
   the call's collection dies. This exists today; the session's one retry covers it. Gate it.
3. **Mine-lock interplay:** the session-side long-operation guards disappear with `_get_client`; the backend-side
   guard (keep client on mtime-only change during a mine) and the reset/reconnect refusals must remain. Re-run G5/G6.
4. **Upstream drift / fork:** upstream may later fix the session cache differently; this patch must be recorded with
   its stacking order (here) and re-checked on every upgrade.

## Interface Comments
```python
def _get_collection(create: bool = False):
    """Return a collection handle for this hub's configured palace, valid for the current tool call.

    Callers do not need to know which backend serves the palace, how its client is cached, when that client is
    rebuilt after an external change, or how the collection was created; all of that lives in the backend.

    create:  True when the caller is about to write and the collection may not exist yet; ignored (forced False)
             in operator read-only mode.
    returns: the collection, or None when the palace cannot be opened; in that case _collection_open_error holds a
             JSON-ready {"error", "details", "hint"} for the tool to return verbatim.

    Design note: the handle is re-acquired on every call and must not be stored across calls. The backend may close
    and replace its client between calls (external writer, repair, post-mine validation, reconnect); re-asking is what
    keeps exactly one chromadb System per palace in this process (#2002).
    """

class ChromaBackend:
    def reset_palace(self, palace) -> None:
        """Forget every cached client for `palace` and chromadb's shared System for it, so the next get_collection
        re-reads the palace from disk.

        Callers do not need to know about chromadb's process-global SharedSystemClient cache or the order in which
        clients must be closed before it is cleared.

        palace: PalaceRef or legacy path string; a palace with no cached client is a no-op.

        Design note: this is the single reset entry point for the hub (#1315 retry, mempalace_reconnect). It must not
        be called while a long operation (mempalace_mine) runs on another thread; hub_lock-aware callers check that.
        """
```
- **What each module hides:** `ChromaBackend` — client/System lifetime, freshness detection, HNSW open checks,
  EF resolution, create-vs-open split, thread pinning, shared-cache reset ordering. `_session._get_collection` —
  read-only policy, error reporting for tools, one retry. `palace.get_collection` — name guard (#2347), backend
  selection, embedder identity.
- **Temporal decomposition risks:** re-introducing a session-level "acquire once, reuse" cache for speed (that is
  Design B); splitting reset into "close" then "clear" steps at call sites instead of `reset_palace`.
- **Deepest abstraction opportunity:** move the HNSW open checks and capacity probe behind the same backend boundary
  as one "palace store" — that is the measured hot path (9.5 s open, 3.4 s capacity probe) and the natural unit for
  the planned Rust port.

## Information Hiding Map
| Module | Hidden Design Decision |
|--------|----------------------|
| `backends/chroma.py: ChromaBackend` | one client per palace; when it is stale; how to rebuild/reset it safely |
| `palace/collection.py: get_collection` | which backend; collection-name policy; embedder identity |
| `mcp_server/_session.py: _get_collection` | tool-facing error shape; read-only mode; retry |
| `hub_lock` (existing patch) | when a reset is unsafe because a mine is writing |

## Temporal Decomposition Watch Points
- Do not cache the returned handle in the session "for speed"; memoize inside the backend if needed.
- Do not reintroduce separate close/clear calls at reset sites; call `reset_palace`.

## Gates (to write before implementing)
1. Five consecutive saves ≤ 6 s each, ≤ 1 client open across them (negative control already measured: 23.5–47.1 s,
   5 opens on the current hub).
2. Save → search → save interleaved ×3: saves ≤ 6 s.
3. **Ordering the review found:** an external mtime change seen first by a *search* → the next *save* runs on a
   rebuilt client (log shows the rebuild; the drawer reads back).
4. Hub mine → save → read-back + search finds it; HNSW/sqlite counts consistent (repair-status OK).
5. Inode replacement on a COPY of the palace (never the live one): next call rebuilds; writes land in the new file.
6. Mine-lock regression: G5 unit + G6 live pass.
7. Per-call reacquire cost measured and recorded (Remaining Risk 1).

## Rust port seam
`ChromaBackend.get_collection / close_palace / reset_palace` (+ the HNSW open checks and capacity probe behind them)
is the single "palace store" boundary. After Design A lands, re-measure; port whatever remains hot behind that
boundary in the MemPalace fork, keeping these three signatures.
