"""apply.py MEMPALACE_PKG_DIR — apply the single-client-cache patch (see DESIGN.md).

Stacks on ../2026-11-20-hub-mine-lock. Every edit is located by an anchor that must match exactly once
(optional blank lines are allowed between anchor lines); all edits are computed first and the files are
written only if every anchor matched, so a mismatch leaves the install untouched.
"""
import re
import sys

M = sys.argv[1]
FILES = ["backends/chroma.py", "mcp_server/_session.py", "mcp_server/_guards.py", "mcp_server/tools_diary.py"]
src = {k: open(f"{M}/{k}").read() for k in FILES}
out = dict(src)


def _rx(text):
    lines = text.split("\n")
    return re.compile(r"\n(?:[ \t]*\n)*".join(re.escape(l) for l in lines))


def rep(key, old, new):
    rx = _rx(old)
    hits = rx.findall(out[key])
    assert len(hits) == 1, (key, old[:80], len(hits))
    out[key] = rx.sub(lambda _m: new, out[key], count=1)


def cut(key, start, end_exclusive, new):
    s = out[key]
    assert s.count(start) == 1, (key, start[:80], s.count(start))
    a = s.index(start)
    b = s.index(end_exclusive, a)
    out[key] = s[:a] + new + s[b:]


# ---------------- backends/chroma.py: one reset entry point on the deep module ----------------
rep("backends/chroma.py",
"""        _close_client(self._clients.pop(path, None))
        self._freshness.pop(path, None)
    def close(self) -> None:""",
'''        _close_client(self._clients.pop(path, None))
        self._freshness.pop(path, None)

    def reset_palace(self, palace) -> None:
        """Forget every cached client and chromadb's shared System, so the next
        ``get_collection`` re-reads ``palace`` from disk.

        Callers do not need to know about chromadb's process-global
        ``SharedSystemClient`` cache or that every owned client must be closed
        before that cache is cleared (see :func:`_clear_chroma_system_cache`).

        Accepts ``PalaceRef`` or legacy path str; a palace with no cached client
        is fine (the reset still drops the shared System for a fresh re-read).

        Local patch (single client cache, 2026-11-20): this is the hub's one
        reset entry point (#1315 retry, ``mempalace_reconnect``). It must not
        run while a long operation (``mempalace_mine``) writes on another
        thread; hub_lock-aware callers check that first.
        """
        path = palace.local_path if isinstance(palace, PalaceRef) else palace
        self._drain_clients()
        _clear_chroma_system_cache()
        if path is not None:
            ChromaBackend._quarantined_paths.discard(path)

    def close(self) -> None:''')

# ---------------- mcp_server/_session.py ----------------
K = "mcp_server/_session.py"
cut(K, "def _get_client():\n", "def _get_collection(create=False):\n", "")
cut(K, '    """Return the configured backend collection, caching handles between calls.', "    global \\\n",
'''    """Return a collection handle for this hub's configured palace, valid for the current tool call.

    Callers do not need to know which backend serves the palace, how its client
    is cached, when that client is rebuilt after an external change, or how the
    collection was created; all of that lives in the backend.

    create: True when the caller is about to write and the collection may not
        exist yet; forced False in operator read-only mode.
    Returns the collection, or None when the palace cannot be opened; then
    ``_collection_open_error`` holds a JSON-ready {"error", "details", "hint"}
    for the tool to return verbatim. One retry follows a failed open.

    Local patch (single client cache, 2026-11-20): for Chroma the handle is
    re-acquired from the backend on every call and must not be stored across
    calls. The backend may close and replace its client between calls
    (external writer, repair, post-mine validation, reconnect); re-asking is
    what keeps exactly one chromadb System per palace in this process (#2002).
    The previous session-owned client never re-baselined after this hub's own
    writes, so every save rebuilt it and reloaded the collection (~17 s).
    """
''')
rep(K, """    global \\
        _client_cache, \\
        _collection_cache, \\
        _collection_cache_backend, \\
        _collection_cache_palace, \\
        _collection_open_error, \\
        _palace_db_inode, \\
        _palace_db_mtime, \\
        _metadata_cache, \\
        _metadata_cache_time
    # Operator read-only mode""",
"""    global \\
        _collection_cache, \\
        _collection_cache_backend, \\
        _collection_cache_palace, \\
        _collection_open_error, \\
        _metadata_cache, \\
        _metadata_cache_time
    # Operator read-only mode""")
cut(K, "    for attempt in range(2):\n        try:\n            if _collection_cache is not None and (\n",
    "\n\ndef _no_palace():",
'''    # Local patch (single client cache, 2026-11-20): ask the storage backend on
    # every call, exactly like the non-chroma branch above, so the hub holds one
    # chromadb client (one System) per palace. Its freshness is
    # ChromaBackend._client's, which _restamp re-baselines after this process's
    # own writes. Not cleared here: the overview caches (5 s TTL; tools clear
    # them after their own writes). Not run here: the HNSW capacity probe -- the
    # search paths that read its flag refresh it themselves, and the pre-open
    # HNSW quarantine runs inside ChromaBackend._client.
    from ..palace import get_collection as palace_get_collection

    _collection_cache = None
    _collection_cache_backend = None
    _collection_cache_palace = None
    open_failed = {
        "error": "Backend open failed",
        "details": "Could not open the Chroma collection.",
        "hint": "Run: mempalace repair-status for diagnostics.",
    }
    for attempt in range(2):
        try:
            collection = palace_get_collection(
                _config.palace_path,
                collection_name=_config.collection_name,
                create=create,
                backend="chroma",
                read_only=_READ_ONLY,
            )
            _collection_open_error = None
            return collection
        except (BackendMismatchError, KeyError) as exc:
            logger.warning("backend open failed for %s: %s", _config.palace_path, exc)
            _collection_open_error = {
                "error": "Backend mismatch"
                if isinstance(exc, BackendMismatchError)
                else "Unknown backend",
                "details": str(exc),
                "hint": "Select the matching backend or use a fresh palace directory.",
            }
            return None
        except Exception:
            logger.exception(
                "_get_collection attempt %d/2 failed (palace=%s, create=%s)",
                attempt + 1,
                _config.palace_path,
                create,
            )
            if attempt == 0:
                # Heal the stale-handle case by re-reading the palace from disk
                # on the second attempt -- unless a mine on another thread is
                # writing through the current client (hub_lock).
                from .. import hub_lock

                if not hub_lock.another_thread_in_long_operation():
                    _force_chroma_cache_reset()
            _collection_open_error = dict(open_failed)
    return None''')

# ---------------- mcp_server/_guards.py ----------------
G = "mcp_server/_guards.py"
rep(G, """    global \\
        _client_cache, \\
        _collection_cache, \\
        _collection_cache_backend, \\
        _collection_cache_palace, \\
        _collection_open_error, \\
        _palace_db_inode, \\
        _palace_db_mtime, \\
        _metadata_cache, \\
        _metadata_cache_time
    cached_client = _client_cache
    try:
        from ..palace import clear_validated_embedder_identity, get_backend_for_palace""",
"""    global \\
        _collection_cache, \\
        _collection_cache_backend, \\
        _collection_cache_palace, \\
        _collection_open_error, \\
        _metadata_cache, \\
        _metadata_cache_time

    try:
        from ..palace import clear_validated_embedder_identity, get_backend_for_palace""")
rep(G, """    if cached_client is not None:
        try:
            close = getattr(cached_client, "close", None)
            if callable(close):
                close()
        except Exception:
            logger.debug(
                "Failed to close MCP-local client while changing ownership",
                exc_info=True,
            )
    _client_cache = None
    _collection_cache = None
    _collection_cache_backend = None
    _collection_cache_palace = None
    _collection_open_error = None
    _palace_db_inode = 0
    _palace_db_mtime = 0.0
    _invalidate_overview_caches()""",
"""    _collection_cache = None
    _collection_cache_backend = None
    _collection_cache_palace = None
    _collection_open_error = None
    _invalidate_overview_caches()""")
rep(G, """_client_cache = None
_collection_cache = None
_collection_cache_backend = None
_collection_cache_palace = None
_collection_open_error = None
_palace_db_inode = 0  # inode of chroma.sqlite3 at cache time
_palace_db_mtime = 0.0  # mtime of chroma.sqlite3 at cache time""",
"""_collection_cache = None
_collection_cache_backend = None
_collection_cache_palace = None
_collection_open_error = None""")
cut(G, "def _force_chroma_cache_reset() -> None:\n", "\n\n# ── Vector-search disabled flag (#1222)",
'''def _force_chroma_cache_reset() -> None:
    """Drop every cached view of the palace so the next call re-reads it from disk.

    Local patch (single client cache, 2026-11-20): the hub keeps one chromadb
    client per palace, owned by the storage backend, so this resets that one
    client (and chromadb's shared System) via ``reset_palace``. Callers that
    can run while a mine writes on another thread check hub_lock first.
    """
    global \\
        _collection_cache, \\
        _collection_cache_backend, \\
        _collection_cache_palace, \\
        _collection_open_error, \\
        _metadata_cache, \\
        _metadata_cache_time
    _collection_cache = None
    _collection_cache_backend = None
    _collection_cache_palace = None
    _collection_open_error = None
    _invalidate_overview_caches()
    # This runs on the #1315 retry path, which drops caches precisely to
    # re-observe the palace after a transient index error. The capacity verdict
    # is another cached view of that same palace, so it must be dropped too, or
    # the retry could be answered from the pre-error verdict (its 10 s ceiling
    # outlasts the 2 s retry sleep).
    reset_hnsw_capacity_cache()
    try:
        from ..palace import get_backend_for_palace

        backend = get_backend_for_palace(_config.palace_path)
        palace_ref = PalaceRef(id=_config.palace_path, local_path=_config.palace_path)
        reset = getattr(backend, "reset_palace", None)
        if callable(reset):
            reset(palace_ref)
        else:
            backend.close_palace(palace_ref)
    except Exception:
        logger.debug("Failed to reset the cached backend client during cache reset", exc_info=True)''')
rep(G, """    Called from :func:`_get_client` whenever the client cache is rebuilt
    (first open or palace replacement).""",
"""    Called by the search paths that read the flag, at startup, and after
    reconnect (local patch 2026-11-20: the session-owned client cache that used
    to call it on every rebuild is gone).""")

# ---------------- mcp_server/tools_diary.py (reconnect) ----------------
D = "mcp_server/tools_diary.py"
rep(D, """        _client_cache, \\
        _collection_cache, \\
        _collection_cache_backend, \\
        _collection_cache_palace, \\
        _collection_open_error, \\
        _palace_db_inode, \\
        _palace_db_mtime, \\
        _vector_disabled, \\""",
"""        _collection_cache, \\
        _collection_cache_backend, \\
        _collection_cache_palace, \\
        _collection_open_error, \\
        _vector_disabled, \\""")
rep(D, """    if _client_cache is not None:
        try:
            close = getattr(_client_cache, "close", None)
            if callable(close):
                close()
        except Exception as exc:
            logger.debug("Failed to close MCP-local Chroma client during reconnect", exc_info=True)
            close_errors.append(f"local Chroma client close failed: {exc}")
""", "")
rep(D, """    _client_cache = None
    _collection_cache = None
    _collection_cache_backend = None
    _collection_cache_palace = None
    _collection_open_error = None
    _palace_db_inode = 0
    _palace_db_mtime = 0.0
    ChromaBackend._quarantined_paths.discard(_config.palace_path)
    # Force probe re-run on next _get_client by clearing the flag now;""",
"""    _collection_cache = None
    _collection_cache_backend = None
    _collection_cache_palace = None
    _collection_open_error = None
    ChromaBackend._quarantined_paths.discard(_config.palace_path)
    # Force a probe re-run by clearing the flag now;""")

for k in FILES:
    if out[k] != src[k]:
        open(f"{M}/{k}", "w").write(out[k])
        print("patched", k)
