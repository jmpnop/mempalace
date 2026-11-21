"""Build the G4 inputs: a synthetic palace (a clone of the frozen palace with rows added
straight into sqlite) and the case lists. Wing names stay in the case files under WORK;
everything printed uses anonymous labels.

usage: g4_cases.py FROZEN SYNTHETIC_DIR CASES_DIR WALK_LIMIT
"""

import json
import os
import sqlite3
import sys

FROZEN, SYNTH, CASES, WALK_LIMIT = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
QUERIES = [
    "mine lock yield during integrity check",
    "chromadb write bindings hold the GIL",
    "Rust port of the bottleneck",
    "slow MCP saves session client rebuild",
    "hub restart launchd",
    "corpus markdown conversion pandoc",
]
NO_TOKEN = "a b"
GREEK = "λόγος σοφίας"
BIG_ROWS = 600_000


def ro(path):
    return sqlite3.connect(f"file:{os.path.join(path, 'chroma.sqlite3')}?mode=ro", uri=True)


def wing_sizes(conn):
    """{wing: (closet rows, rows in every collection)} for string wings."""
    sizes = {}
    for wing, collection, n in conn.execute(
        """SELECT m.string_value, c.name, count(*) FROM embedding_metadata m
           JOIN embeddings e ON e.id = m.id JOIN segments s ON s.id = e.segment_id
           JOIN collections c ON c.id = s.collection
           WHERE m.key = 'wing' AND m.string_value IS NOT NULL GROUP BY 1, 2"""
    ):
        closets, total = sizes.get(wing, (0, 0))
        sizes[wing] = (closets + (n if collection == "mempalace_closets" else 0), total + n)
    return sizes


def a_room(conn, wing, collection):
    row = conn.execute(
        """SELECT r.string_value FROM embedding_metadata w
           JOIN embedding_metadata r ON r.id = w.id AND r.key = 'room'
           JOIN embeddings e ON e.id = w.id JOIN segments s ON s.id = e.segment_id
           JOIN collections c ON c.id = s.collection
           WHERE w.key = 'wing' AND w.string_value = ? AND c.name = ? LIMIT 1""",
        (wing, collection),
    ).fetchone()
    return row[0] if row else None


def build_synthetic():
    os.system(f"rm -rf '{SYNTH}' && cp -cR '{FROZEN}' '{SYNTH}' && chmod -R u+w '{SYNTH}'")
    sys.path.insert(0, os.environ["FORK_TREE"])
    from mempalace.stored_text import encode_record

    conn = sqlite3.connect(os.path.join(SYNTH, "chroma.sqlite3"))
    seg = dict(
        conn.execute(
            """SELECT c.name, s.id FROM segments s JOIN collections c ON c.id = s.collection
               WHERE s.scope = 'METADATA'"""
        )
    )
    next_id = conn.execute("SELECT max(id) FROM embeddings").fetchone()[0] + 1

    def add(collection, doc, meta, ints=()):
        nonlocal next_id
        i = next_id
        next_id += 1
        conn.execute(
            "INSERT INTO embeddings (id, segment_id, embedding_id, seq_id) VALUES (?, ?, ?, ?)",
            (i, seg[collection], f"g4-{collection}-{i}", i.to_bytes(8, "big")),
        )
        conn.execute(
            "INSERT INTO embedding_metadata (id, key, string_value) VALUES (?, 'chroma:document', ?)",
            (i, doc),
        )
        conn.execute(
            "INSERT INTO embedding_fulltext_search (rowid, string_value) VALUES (?, ?)", (i, doc)
        )
        for key, value in meta.items():
            column = "int_value" if key in ints else "string_value"
            if isinstance(value, bool):
                column, value = "bool_value", int(value)
            elif isinstance(value, float):
                column = "float_value"
            conn.execute(
                f"INSERT INTO embedding_metadata (id, key, {column}) VALUES (?, ?, ?)",
                (i, key, value),
            )

    conn.execute("BEGIN")
    big_rows = int(os.environ.get("G4_BIG_ROWS", BIG_ROWS))  # G5 builds with 0: no vectors for them
    for n in range(big_rows):  # a closet-less wing far past the walk limit
        add(
            "mempalace_drawers",
            f"synthetic big drawer {n} hub restart launchd",
            {"wing": "zz_big", "room": "r"},
        )
    for n in range(30):  # AAAK summaries that crowd the closet window, then real closets
        add(
            "mempalace_closets",
            f"aaak summary {n} hub restart launchd",
            {
                "wing": "zz_aaak",
                "room": "r",
                "compression_ratio": 3.5,
                "source_file": f"/syn/aaak{n}.md",
            },
        )
    for n in range(6):
        add(
            "mempalace_closets",
            f"closet pointer {n} hub restart launchd",
            {"wing": "zz_aaak", "room": "r", "source_file": f"/syn/closet{n}.md"},
        )
    doc, meta = encode_record(
        "escaped wing closet hub restart",
        {"wing": "a\x00b", "room": "r", "source_file": "/syn/esc.md"},
    )
    add("mempalace_closets", doc, meta)
    add(
        "mempalace_closets",
        "numeric wing closet hub restart",
        {"wing": 7, "room": "r", "source_file": "/syn/int7.md"},
        ints=("wing",),
    )
    add(
        "mempalace_closets",
        "string seven wing closet hub restart",
        {"wing": "7", "room": "r", "source_file": "/syn/str7.md"},
    )
    add(
        "mempalace_closets",
        "sigma closet λόγος σοφίας",
        {"wing": "zz_greek", "room": "r", "source_file": "/syn/greek.md"},
    )
    conn.commit()
    conn.close()
    os.system(f"chmod -R a-w '{SYNTH}'")
    with open(os.path.join(CASES, ".synthetic-built"), "w") as f:
        f.write("ok")


def main():
    os.makedirs(CASES, exist_ok=True)
    if not os.path.exists(os.path.join(CASES, ".synthetic-built")):
        build_synthetic()
    conn = ro(FROZEN)
    sizes = wing_sizes(conn)
    wings = sorted(sizes)
    labels = {w: f"wing{n:02d}" for n, w in enumerate(wings)}
    with_closets = sorted((w for w in wings if sizes[w][0]), key=lambda w: sizes[w][0])
    without = [w for w in wings if not sizes[w][0]]
    print(f"clone: {len(wings)} wings, {len(with_closets)} with closets, {len(without)} without")
    for w in wings:
        closets, total = sizes[w]
        band = (
            "no-closets"
            if not closets
            else (
                "closets-large"
                if closets > 10_000
                else "closets-mid"
                if closets > 1_000
                else "closets-small"
            )
        )
        walk = "in-limit" if total < WALK_LIMIT else "past-limit"
        print(f"  {labels[w]} {band:13s} closets={closets:6d} all-collections={total:7d} {walk}")

    eq, timed = [], []
    for w in wings:  # every wing x every profiled query, equality only
        for qi, q in enumerate(QUERIES):
            eq.append(
                {
                    "name": f"{labels[w]}/q{qi}",
                    "query": q,
                    "where": {"wing": w},
                    "equality_only": True,
                }
            )
    timed.append({"name": "palace-wide/q2", "query": QUERIES[2], "where": None})
    timed.append({"name": "palace-wide/q3", "query": QUERIES[3], "where": None})
    for w in wings:
        closets, total = sizes[w]
        kind = (
            ("noclosets-inlimit" if total < WALK_LIMIT else "noclosets-pastlimit")
            if not closets
            else "closets"
        )
        timed.append({"name": f"{kind}/{labels[w]}/q1", "query": QUERIES[1], "where": {"wing": w}})
    for qi, q in enumerate(QUERIES):
        eq.append({"name": f"palace-wide/q{qi}", "query": q, "where": None, "equality_only": True})
    big = with_closets[-1]
    small_free = min(without, key=lambda w: sizes[w][1])
    room = a_room(conn, big, "mempalace_closets")
    eq += [
        {
            "name": "and-wing-room/closets",
            "query": QUERIES[4],
            "where": {"$and": [{"wing": big}, {"room": room}]},
            "equality_only": True,
        },
        {
            "name": "and-wing-room/noclosets",
            "query": QUERIES[4],
            "where": {
                "$and": [
                    {"wing": small_free},
                    {"room": a_room(conn, small_free, "mempalace_drawers") or "x"},
                ]
            },
            "equality_only": True,
        },
        {
            "name": "in/closets+noclosets",
            "query": QUERIES[1],
            "where": {"wing": {"$in": [small_free, big]}},
            "equality_only": True,
        },
        {
            "name": "in/noclosets+noclosets",
            "query": QUERIES[1],
            "where": {"wing": {"$in": without[:2]}},
            "equality_only": True,
        },
        {
            "name": "no-token/closets",
            "query": NO_TOKEN,
            "where": {"wing": big},
            "equality_only": True,
        },
        {
            "name": "no-token/noclosets",
            "query": NO_TOKEN,
            "where": {"wing": small_free},
            "equality_only": True,
        },
        {"name": "greek/palace-wide", "query": GREEK, "where": None, "equality_only": True},
        {
            "name": "greek/noclosets",
            "query": GREEK,
            "where": {"wing": small_free},
            "equality_only": True,
        },
        {
            "name": "missing-wing",
            "query": QUERIES[1],
            "where": {"wing": "zz_no_such_wing"},
            "equality_only": True,
        },
        {"name": "numeric-wing", "query": QUERIES[1], "where": {"wing": 5}, "equality_only": True},
        {
            "name": "backslash-wing",
            "query": QUERIES[1],
            "where": {"wing": "a\\b"},
            "equality_only": True,
        },
        {
            "name": "union-drawers/biggest-noclosets",
            "query": QUERIES[2],
            "collection": "mempalace_drawers",
            "where": {"wing": max(without, key=lambda w: sizes[w][1])},
            "boosts": False,
            "limit": 15,
        },
        {
            "name": "union-drawers/missing",
            "query": QUERIES[2],
            "collection": "mempalace_drawers",
            "where": {"wing": "zz_no_such_wing"},
            "boosts": False,
            "limit": 15,
        },
    ]
    synthetic = [
        {"name": "syn/big-pastlimit", "query": QUERIES[4], "where": {"wing": "zz_big"}},
        {"name": "syn/aaak-refetch", "query": QUERIES[4], "where": {"wing": "zz_aaak"}},
        {"name": "syn/escaped-wing", "query": QUERIES[4], "where": {"wing": "a\x00b"}},
        {"name": "syn/int-wing", "query": QUERIES[4], "where": {"wing": 7}},
        {"name": "syn/str7-wing", "query": QUERIES[4], "where": {"wing": "7"}},
        {
            "name": "syn/in-aaak-big",
            "query": QUERIES[4],
            "where": {"wing": {"$in": ["zz_aaak", "zz_big"]}},
        },
        {
            "name": "syn/and-empty-room",
            "query": QUERIES[4],
            "where": {"$and": [{"wing": "zz_aaak"}, {"room": "nowhere"}]},
        },
        {"name": "syn/greek", "query": GREEK, "where": {"wing": "zz_greek"}},
        {"name": "syn/palace-wide", "query": QUERIES[4], "where": None},
    ]
    for case in synthetic:
        case["equality_only"] = True
    for name, cases in (("eq", eq), ("timed", timed), ("synthetic", synthetic)):
        with open(os.path.join(CASES, f"{name}.json"), "w") as f:
            json.dump(cases, f)
    print(f"cases: equality {len(eq)}, timed {len(timed)}, synthetic {len(synthetic)}")


main()
