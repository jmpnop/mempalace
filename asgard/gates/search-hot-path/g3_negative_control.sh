#!/usr/bin/env bash
# G3: the FORK's new tests, run against the BASE source, fail exactly where the behaviour is
# new and only by AssertionError; everything else passes on both. The tests themselves come
# from the FORK in both runs; only the mempalace package differs.
source "$(dirname "$0")/lib.sh"
ensure_base
out="$WORK/g3"; rm -rf "$out"; mkdir -p "$out"
TESTS=(tests/test_hnsw_capacity.py tests/test_chroma_sqlite_scope_walk.py)

run_tests() {  # side junit-out
  # From a neutral directory: `python -m` would put the cwd (the fork) first on sys.path.
  local tree; tree="$(tree_of "$1")"
  mkdir -p "$out/cwd"
  ( cd "$out/cwd" && G3_WHICH_OUT="$out/$1.which" PYTHONPATH="$tree:$GATES" \
      "$PY" -m pytest -q -p no:cacheprovider -p g3_which --rootdir "$REPO" -o pythonpath= \
      --junitxml "$2" "${TESTS[@]/#/$REPO/}" > "$out/$1.log" 2>&1 ) || true
  local which; which="$(cat "$out/$1.which" 2>/dev/null || true)"
  echo "$1 run imported mempalace from: $which"
  case "$which" in "$(cd "$tree" && pwd -P)"/mempalace/*) ;; *) die "G3_FAILED $1 run imported the wrong tree" ;; esac
}
run_tests base "$out/base.xml"
run_tests fork "$out/fork.xml"

"$PY" -I - "$out" "$BASE" "$REPO" <<'EOF'
import sys, xml.etree.ElementTree as ET
out, base_tree, fork_tree = sys.argv[1:4]
NEW = {
    "TestCapacityReuseWindow::test_allow_listed_fs_reuses_an_unchanged_verdict_past_ten_seconds",
    "TestCapacityReuseWindow::test_explicit_threshold_with_an_old_pickle_is_reused",
    "TestCapacityReuseWindow::test_young_pickle_divergence_is_reused_before_the_grace_ends",
    "TestCapacityReuseWindow::test_young_pickle_divergence_reprobes_when_the_grace_ends",
    "TestCapacityReuseWindow::test_an_already_diverged_verdict_is_reused_within_its_window",
    "TestCapacityReuseWindow::test_each_uncached_probe_logs_its_reason",
    "TestCapacityReuseWindow::test_fs_type_prefers_the_inode64_symbol",
    "TestCapacityReuseWindow::test_fs_type_is_empty_for_a_missing_path",
    "test_empty_scope_skips_the_full_text_query",
    "test_one_exhausted_equality_in_an_and_skips",
    "test_limit_outcome_is_counted_and_falls_back",
    "test_limit_outcome_logs_once_per_scope",
    "test_walk_error_is_counted_and_falls_back",
    "test_wing_names_first_query_uses_the_partial_index",
}

def results(path):
    res = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        cls = case.get("classname", "").rsplit(".", 1)[-1]
        name = case.get("name")
        key = f"{cls}::{name}" if cls.startswith("Test") else name
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        if failure is not None:
            # pytest ends the failure text with "<file>:<line>: <ExceptionType>".
            last = (failure.text or "").rstrip().rsplit("\n", 1)[-1]
            res[key] = ("failed", last.rsplit(": ", 1)[-1].strip())
        elif error is not None:
            res[key] = ("error", (error.get("message") or "")[:80])
        elif skipped is not None:
            res[key] = ("skipped", "")
        else:
            res[key] = ("passed", "")
    return res

base, fork = results(f"{out}/base.xml"), results(f"{out}/fork.xml")
ok = True
if set(base) != set(fork):
    ok = False; print("test sets differ:", set(base) ^ set(fork))
missing = NEW - set(fork)
if missing:
    ok = False; print("new-behaviour tests not found:", sorted(missing))
print(f"tests: {len(fork)}; new-behaviour: {len(NEW)}; preserved: {len(fork) - len(NEW)}")
for key in sorted(fork):
    b, f = base.get(key, ("absent", "")), fork[key]
    if f[0] != "passed":
        ok = False; print(f"FORK {f[0]}: {key} {f[1]}")
    if key in NEW:
        print(f"  new       BASE {b[0]:7s} {b[1]:15s} FORK {f[0]}  {key}")
        if b != ("failed", "AssertionError"):
            ok = False; print(f"    -> BASE must fail by AssertionError, got {b}")
    elif b[0] != "passed":
        ok = False; print(f"  preserved BASE {b[0]} {b[1]}  {key}")
print("G3_NEGATIVE_CONTROL_OK" if ok else "G3_FAILED")
sys.exit(0 if ok else 1)
EOF
