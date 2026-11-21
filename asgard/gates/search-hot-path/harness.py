"""One side (BASE or FORK) of a search-hot-path gate comparison.

Run with ``python -I harness.py <mode> --tree TREE --palace PALACE ...``. The
interpreter is isolated (no PYTHONPATH, no user site, no cwd on the path); the
tree under test is put first on ``sys.path`` explicitly and the run aborts unless
``mempalace`` is imported from that tree. HOME and the MemPalace config
directory are redirected to a scratch directory beside the working copy, so a
run can never see the live hub's state, locks or palace. Output is one JSON
document on stdout.

Modes:
  wing_names   sqlite_wing_names result, its first query's plan, timings
  lexical      sqlite_lexical_rows and _closet_boosts per case, timings
  search       tool_search JSON per case (timing fields removed)
  walk_limit   time of one bounded scope walk that reaches its limit (FORK only)
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time


def _isolate(tree: str, palace: str, scratch: str) -> None:
    home = os.path.join(scratch, "home")
    config_dir = os.path.join(home, ".config", "mempalace")
    os.makedirs(config_dir, exist_ok=True)
    real_cache = os.path.join(os.path.expanduser("~"), ".cache")
    cache_link = os.path.join(home, ".cache")
    if os.path.isdir(real_cache) and not os.path.lexists(cache_link):
        os.symlink(real_cache, cache_link)  # embedding models only; read
    with open(os.path.join(config_dir, "config.json"), "w") as f:
        json.dump({"palace_path": palace}, f)
    os.environ["HOME"] = home
    os.environ["MEMPALACE_CONFIG_DIR"] = config_dir
    os.environ["MEMPALACE_PALACE_PATH"] = palace
    for name in ("MEMPALACE_LANG", "MEMPALACE_BACKEND", "MEMPALACE_HUB_URL"):
        os.environ.pop(name, None)
    sys.path.insert(0, os.path.abspath(tree))
    import mempalace

    where = os.path.realpath(mempalace.__file__)
    if not where.startswith(os.path.realpath(tree) + os.sep):
        sys.exit(f"HARNESS_WRONG_TREE mempalace imported from {where}, expected under {tree}")


def _timed(fn, runs: int):
    fn()  # warm-up, discarded
    times, result = [], None
    for _ in range(runs):
        t0 = time.perf_counter()
        result = fn()
        times.append(time.perf_counter() - t0)
    return result, times


def _jsonable(value):
    return json.loads(json.dumps(value, default=repr, sort_keys=True))


def mode_wing_names(args) -> dict:
    import sqlite3

    from mempalace.backends import chroma_sqlite as cs

    seen: list[str] = []
    real_open = cs.open_palace_reader

    def tracing(db_path, *a, **kw):
        conn = real_open(db_path, *a, **kw)
        conn.set_trace_callback(seen.append)
        return conn

    cs.open_palace_reader = tracing
    names = cs.sqlite_wing_names(args.palace)
    cs.open_palace_reader = real_open
    first = next((s for s in seen if "MIN(string_value)" in s), "")
    conn = sqlite3.connect(f"file:{os.path.join(args.palace, 'chroma.sqlite3')}?mode=ro", uri=True)
    try:
        plan = " | ".join(str(r[-1]) for r in conn.execute("EXPLAIN QUERY PLAN " + first))
    finally:
        conn.close()
    _, times = _timed(lambda: cs.sqlite_wing_names(args.palace), args.runs)
    return {"names": names, "first_query": first, "plan": plan, "times": times}


def _cases(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def mode_lexical(args) -> dict:
    import mempalace.searcher as searcher
    from mempalace.backends import chroma_sqlite as cs

    closets = searcher.get_closets_collection(args.palace, create=False, read_only=True)
    out = []
    for case in _cases(args.cases):
        where = case.get("where")
        collection = case.get("collection", "mempalace_closets")
        rows = cs.sqlite_lexical_rows(
            args.palace, collection, case["query"], where=where, limit=case.get("limit", 10)
        )
        entry = {"name": case["name"], "rows": _jsonable(rows)}
        if case.get("boosts", True) and collection == "mempalace_closets":

            def boosts(case=case, where=where):
                return searcher._closet_boosts(
                    closets, query=case["query"], n_results=case.get("n_results", 5), where=where
                )

            if case.get("equality_only"):
                entry["boosts"] = _jsonable(boosts())
            else:
                result, times = _timed(boosts, args.runs)
                entry["boosts"] = _jsonable(result)
                entry["times"] = times
        out.append(entry)
    return {"cases": out, "outcomes": dict(getattr(cs, "SCOPE_WALK_OUTCOMES", {}))}


_TIMING_KEYS = {"elapsed", "elapsed_ms", "timing", "timings", "took_ms", "duration_ms", "run_s"}


def _strip_timing(value):
    if isinstance(value, dict):
        return {k: _strip_timing(v) for k, v in value.items() if k not in _TIMING_KEYS}
    if isinstance(value, list):
        return [_strip_timing(v) for v in value]
    return value


def mode_search(args) -> dict:
    from mempalace import mcp_server

    out = []
    for case in _cases(args.cases):
        kwargs = {k: v for k, v in case.items() if k not in ("name",)}
        result = mcp_server.tool_search(**kwargs)
        out.append(
            {
                "name": case["name"],
                "result": _strip_timing(_jsonable(result)),
                "vector_disabled": bool(getattr(mcp_server, "_vector_disabled", False)),
            }
        )
    return {"cases": out}


def mode_walk_limit(args) -> dict:
    from mempalace.backends import chroma_sqlite as cs

    if not hasattr(cs, "_scope_walk"):
        sys.exit("HARNESS_NO_SCOPE_WALK")
    conn = cs.open_palace_reader(os.path.join(args.palace, "chroma.sqlite3"))
    try:
        result, times = _timed(
            lambda: cs._scope_walk(
                conn, "mempalace_closets", "wing", (args.value,), cs._SCOPE_PROBE_LIMIT
            ),
            args.runs,
        )
    finally:
        conn.close()
    return {"result": result, "times": times, "median": statistics.median(times)}


def mode_mix(args) -> dict:
    """Repeat the G12 search mix for ``--duration`` seconds; report what ran."""
    import mempalace.searcher as searcher
    from mempalace.backends import chroma_sqlite as cs

    closets = searcher.get_closets_collection(args.palace, create=False, read_only=True)
    mix = _cases(args.cases)
    ran, deadline = [], time.monotonic() + args.duration
    while time.monotonic() < deadline:
        for case in mix:
            t0 = time.perf_counter()
            if case.get("collection") == "mempalace_drawers":
                cs.sqlite_lexical_rows(
                    args.palace,
                    "mempalace_drawers",
                    case["query"],
                    where=case.get("where"),
                    limit=case.get("limit", 15),
                )
            else:
                searcher._closet_boosts(
                    closets, query=case["query"], n_results=5, where=case.get("where")
                )
            ran.append((case["name"], time.perf_counter() - t0))
            if time.monotonic() >= deadline:
                break
    return {"ran": ran}


def mode_capacity(args) -> dict:
    """The capacity verdict for both collections and each VECTOR segment's pickle count."""
    import sqlite3

    from mempalace.backends import chroma as chroma_mod

    conn = sqlite3.connect(f"file:{os.path.join(args.palace, 'chroma.sqlite3')}?mode=ro", uri=True)
    try:
        segments = [r[0] for r in conn.execute("SELECT id FROM segments WHERE scope = 'VECTOR'")]
    finally:
        conn.close()
    return {
        "status": {
            name: _jsonable(chroma_mod.hnsw_capacity_status(args.palace, name))
            for name in ("mempalace_drawers", "mempalace_closets")
        },
        "pickle_counts": {s: chroma_mod._hnsw_element_count(args.palace, s) for s in segments},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode", choices=["wing_names", "lexical", "search", "walk_limit", "mix", "capacity"]
    )
    parser.add_argument("--tree", required=True)
    parser.add_argument("--palace", required=True)
    parser.add_argument("--scratch", required=True)
    parser.add_argument("--cases")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--value", default="")
    parser.add_argument("--duration", type=float, default=120.0)
    args = parser.parse_args()
    _isolate(args.tree, args.palace, args.scratch)
    result = globals()[f"mode_{args.mode}"](args)
    import mempalace

    result["mempalace_file"] = mempalace.__file__
    json.dump(result, sys.stdout, sort_keys=True)


if __name__ == "__main__":
    main()
