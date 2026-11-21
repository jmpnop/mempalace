"""The empty-scope skip in ``sqlite_lexical_rows`` and the indexed wing-name walk.

A scoped lexical query whose filter names a value no row of the collection holds
cannot match anything, so the full-text query is skipped after a bounded index
walk. These tests pin the three outcomes of that walk (skip, fall back at the
limit, fall back on an error), that the output never changes, and that the
wing-name enumeration uses the partial ``(key, string_value)`` index.

New names are reached through ``getattr`` with a default and patched with
``raising=False`` so that, against a tree without the change, every new-behaviour
test fails by assertion rather than by an import or attribute error.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from mempalace.backends import chroma_sqlite as cs

DRAWERS = "mempalace_drawers"
CLOSETS = "mempalace_closets"


def _build_palace(path: str, rows: list[tuple[str, str, dict]], *, index: bool = True) -> str:
    """A chroma-shaped ``chroma.sqlite3`` holding ``(collection, text, metadata)`` rows.

    Both collections get a METADATA segment that their rows point at, as chromadb
    1.5.x writes them; every document is also in the shared trigram index.
    """
    os.makedirs(path, exist_ok=True)
    conn = sqlite3.connect(os.path.join(path, "chroma.sqlite3"))
    try:
        conn.executescript(
            """
            CREATE TABLE collections (id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE collection_metadata (
                collection_id TEXT, key TEXT NOT NULL, str_value TEXT, int_value INTEGER,
                float_value REAL, bool_value INTEGER, PRIMARY KEY (collection_id, key));
            CREATE TABLE segments (id TEXT PRIMARY KEY, collection TEXT NOT NULL, scope TEXT NOT NULL);
            CREATE TABLE embeddings (
                id INTEGER PRIMARY KEY, segment_id TEXT NOT NULL, embedding_id TEXT NOT NULL,
                seq_id BLOB NOT NULL, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE embedding_metadata (
                id INTEGER REFERENCES embeddings(id), key TEXT NOT NULL, string_value TEXT,
                int_value INTEGER, float_value REAL, bool_value INTEGER, PRIMARY KEY (id, key));
            CREATE VIRTUAL TABLE embedding_fulltext_search USING fts5(string_value, tokenize='trigram');
            """
        )
        if index:
            conn.execute(
                "CREATE INDEX embedding_metadata_string_value ON embedding_metadata"
                " (key, string_value) WHERE string_value IS NOT NULL"
            )
        for name in (DRAWERS, CLOSETS):
            conn.execute("INSERT INTO collections VALUES (?, ?)", (f"col-{name}", name))
            conn.execute(
                "INSERT INTO segments VALUES (?, ?, 'METADATA')", (f"meta-{name}", f"col-{name}")
            )
            conn.execute(
                "INSERT INTO segments VALUES (?, ?, 'VECTOR')", (f"vec-{name}", f"col-{name}")
            )
        for i, (collection, text, meta) in enumerate(rows, start=1):
            conn.execute(
                "INSERT INTO embeddings (id, segment_id, embedding_id, seq_id) VALUES (?, ?, ?, ?)",
                (i, f"meta-{collection}", f"{collection}-{i}", b"\x00" * 8),
            )
            conn.execute(
                "INSERT INTO embedding_metadata (id, key, string_value) VALUES (?, 'chroma:document', ?)",
                (i, text),
            )
            conn.execute(
                "INSERT INTO embedding_fulltext_search (rowid, string_value) VALUES (?, ?)",
                (i, text),
            )
            for key, value in meta.items():
                column = "int_value" if isinstance(value, int) else "string_value"
                conn.execute(
                    f"INSERT INTO embedding_metadata (id, key, {column}) VALUES (?, ?, ?)",
                    (i, key, value),
                )
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture
def palace(tmp_path):
    """Drawers in wings ``ops`` (5 rows) and ``design``; closets only in ``design``."""
    rows = [
        (DRAWERS, f"hub restart launchd note {i}", {"wing": "ops", "room": "runbook"})
        for i in range(5)
    ]
    rows += [
        (DRAWERS, "palace design rooms drawers", {"wing": "design", "room": "metaphor"}),
        (CLOSETS, "closet: hub restart design", {"wing": "design", "room": "metaphor"}),
        (CLOSETS, "closet: launchd plist design", {"wing": "design", "room": "infra"}),
    ]
    return _build_palace(str(tmp_path / "palace"), rows)


@pytest.fixture
def statements(monkeypatch):
    """Every SQL statement run on a palace reader opened by ``chroma_sqlite``."""
    seen: list[str] = []
    real_open = cs.open_palace_reader

    def tracing_open(db_path, *args, **kwargs):
        conn = real_open(db_path, *args, **kwargs)
        conn.set_trace_callback(seen.append)
        return conn

    monkeypatch.setattr(cs, "open_palace_reader", tracing_open)
    return seen


def _outcomes() -> dict:
    return dict(getattr(cs, "SCOPE_WALK_OUTCOMES", {}))


@pytest.fixture(autouse=True)
def _fresh_outcomes():
    reset = getattr(cs, "reset_scope_walk_stats", None)
    if reset is not None:
        reset()
    yield


def _lexical(palace, collection, where, query="hub restart launchd"):
    return cs.sqlite_lexical_rows(palace, collection, query, where=where, limit=10)


def _match_ran(statements) -> bool:
    return any(" MATCH " in s.upper() for s in statements)


# ── new behaviour ──────────────────────────────────────────────────────


def test_empty_scope_skips_the_full_text_query(palace, statements):
    """``ops`` holds drawers but no closet: the closet query cannot match."""
    assert _lexical(palace, CLOSETS, {"wing": "ops"}) == []
    assert not _match_ran(statements), "an empty scope must not run the trigram MATCH"
    assert _outcomes().get("skip") == 1


def test_one_exhausted_equality_in_an_and_skips(palace, statements):
    where = {"$and": [{"wing": "design"}, {"room": "runbook"}]}
    assert _lexical(palace, CLOSETS, where) == []
    assert not _match_ran(statements)
    assert _outcomes().get("skip") == 1


def test_limit_outcome_is_counted_and_falls_back(palace, statements, monkeypatch):
    monkeypatch.setattr(cs, "_SCOPE_PROBE_LIMIT", 3, raising=False)
    assert _lexical(palace, CLOSETS, {"wing": "ops"}) == []
    assert _match_ran(statements), "a walk that reached its limit must fall back to the MATCH"
    assert _outcomes().get("limit") == 1


def test_limit_outcome_logs_once_per_scope(palace, monkeypatch, caplog):
    monkeypatch.setattr(cs, "_SCOPE_PROBE_LIMIT", 3, raising=False)
    with caplog.at_level("INFO", logger=cs.__name__):
        _lexical(palace, CLOSETS, {"wing": "ops"})
        _lexical(palace, CLOSETS, {"wing": "ops"})
    lines = [r for r in caplog.records if "scope walk" in r.getMessage()]
    assert len(lines) == 1, [r.getMessage() for r in caplog.records]


def test_walk_error_is_counted_and_falls_back(palace, statements, monkeypatch):
    def failing(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(cs, "_scope_walk", failing, raising=False)
    assert _lexical(palace, CLOSETS, {"wing": "ops"}) == []
    assert _match_ran(statements)
    assert _outcomes().get("error") == 1


def test_wing_names_first_query_uses_the_partial_index(palace, statements):
    names = cs.sqlite_wing_names(palace)
    assert names == ["design", "ops"]
    first = next(s for s in statements if "MIN(string_value)" in s)
    conn = sqlite3.connect(os.path.join(palace, "chroma.sqlite3"))
    try:
        plan = " ".join(str(r[-1]) for r in conn.execute("EXPLAIN QUERY PLAN " + first))
    finally:
        conn.close()
    assert "embedding_metadata_string_value" in plan, plan


# ── behaviour preserved ─────────────────────────────────────────────────


def test_scope_with_rows_returns_today_s_rows(palace):
    rows = _lexical(palace, CLOSETS, {"wing": "design"})
    assert sorted(r[0] for r in rows) == [f"{CLOSETS}-7", f"{CLOSETS}-8"]


def test_in_skips_only_when_every_value_is_absent(palace, statements):
    rows = _lexical(palace, CLOSETS, {"wing": {"$in": ["ops", "design"]}})
    assert sorted(r[0] for r in rows) == [f"{CLOSETS}-7", f"{CLOSETS}-8"]
    assert _match_ran(statements)


def test_limit_fallback_output_matches(palace, monkeypatch):
    expected = _lexical(palace, CLOSETS, {"wing": "ops"})
    monkeypatch.setattr(cs, "_SCOPE_PROBE_LIMIT", 3, raising=False)
    assert _lexical(palace, CLOSETS, {"wing": "ops"}) == expected == []


def test_error_fallback_output_matches(palace, monkeypatch):
    expected = _lexical(palace, CLOSETS, {"wing": "design"})

    def failing(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(cs, "_scope_walk", failing, raising=False)
    assert _lexical(palace, CLOSETS, {"wing": "design"}) == expected


def test_filters_the_walk_cannot_evaluate_take_today_s_path(palace, statements):
    """``$or`` and numeric values are not string equalities: no walk, no skip."""
    assert _lexical(palace, CLOSETS, {"$or": [{"wing": "ops"}, {"wing": "nowhere"}]}) == []
    assert _match_ran(statements)
    statements.clear()
    assert _lexical(palace, CLOSETS, {"wing": 5}) == []
    assert _match_ran(statements)
    assert _outcomes().get("skip", 0) == 0


def test_query_without_a_long_token_still_returns_today_s_rows(palace):
    """The recency path (no 3+-character token) ends in the same scope filter."""
    rows = cs.sqlite_lexical_rows(palace, CLOSETS, "a b", where={"wing": "design"}, limit=10)
    assert sorted(r[0] for r in rows) == [f"{CLOSETS}-7", f"{CLOSETS}-8"]
    assert cs.sqlite_lexical_rows(palace, CLOSETS, "a b", where={"wing": "ops"}, limit=10) == []


def test_wing_names_identical_without_the_index(tmp_path):
    rows = [(DRAWERS, "x", {"wing": w}) for w in ("b", "a", "c")] + [(DRAWERS, "y", {"wing": 7})]
    plain = _build_palace(str(tmp_path / "plain"), rows, index=False)
    indexed = _build_palace(str(tmp_path / "indexed"), rows, index=True)
    assert cs.sqlite_wing_names(plain) == cs.sqlite_wing_names(indexed) == ["a", "b", "c"]
