#!/usr/bin/env bash
# G6: the upstream suite on FORK has no failures other than the two documented in doc 06
# (test_cli_import_under_startup_budget fails on unmodified v3.12.0 on this Intel Mac;
# test_armed_watchdog_waits_for_interpreter_shutdown fails only under -n auto load and must
# pass when re-run alone); ruff check and format are clean on every Python file changed
# since the deployed commit.
source "$(dirname "$0")/lib.sh"
out="$WORK/g6"; rm -rf "$out"; mkdir -p "$out"
cd "$REPO"

"$PY" -m pytest -q -p no:cacheprovider -n auto --junitxml "$out/suite.xml" > "$out/suite.log" 2>&1 || true
tail -3 "$out/suite.log"

"$PY" -I - "$out/suite.xml" > "$out/failed.txt" <<'EOF'
import sys, xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
for case in root.iter("testcase"):
    if case.find("failure") is not None or case.find("error") is not None:
        print(f"{case.get('classname')}::{case.get('name')}")
EOF
ok=1
total="$("$PY" -I -c "import sys,xml.etree.ElementTree as E;r=E.parse(sys.argv[1]).getroot();print(sum(1 for _ in r.iter('testcase')))" "$out/suite.xml")"
echo "testcases: $total; failed or errored: $(wc -l < "$out/failed.txt" | tr -d ' ')"
[ "$total" -gt 8000 ] || { echo "suite too small: $total"; ok=0; }
while read -r failed; do
  [ -n "$failed" ] || continue
  name="${failed##*::}"
  case "$name" in
    test_cli_import_under_startup_budget)
      echo "documented (fails on unmodified v3.12.0 here): $failed" ;;
    test_armed_watchdog_waits_for_interpreter_shutdown)
      # Timing-sensitive (a watchdog SIGKILLs a child after a deadline). Re-run alone, 3 times
      # on each tree from a neutral directory, after the suite's load has drained.
      sleep 30
      declare -A passes=([base]=0 [fork]=0)
      ensure_base
      for side in base fork; do
        for i in 1 2 3; do
          mkdir -p "$out/cwd"
          if ( cd "$out/cwd" && PYTHONPATH="$(tree_of $side)" "$PY" -m pytest -q -p no:cacheprovider \
                 --rootdir "$REPO" -o pythonpath= "$REPO/tests/test_hook_mine_shutdown.py" -k "$name" \
                 > "$out/rerun-$side-$i.log" 2>&1 ); then
            passes[$side]=$((passes[$side] + 1))
          fi
        done
      done
      echo "re-run alone: BASE ${passes[base]}/3, FORK ${passes[fork]}/3 passed"
      if [ "${passes[fork]}" -ge 2 ] && [ "${passes[fork]}" -ge "${passes[base]}" ]; then
        echo "documented (load-sensitive; passes alone): $failed"
      else
        echo "FAILS ALONE: $failed"; ok=0
      fi ;;
    *) echo "UNEXPECTED FAILURE: $failed"; ok=0 ;;
  esac
done < "$out/failed.txt"

changed="$(git diff --name-only "$BASE_COMMIT" -- 'mempalace/*.py' 'tests/*.py' 'asgard/gates/search-hot-path/*.py' | tr '\n' ' ')"
echo "changed Python files: $changed"
# shellcheck disable=SC2086
"$REPO/.venv/bin/ruff" check $changed || ok=0
# shellcheck disable=SC2086
"$REPO/.venv/bin/ruff" format --check $changed || ok=0

[ "$ok" = 1 ] && echo "G6_SUITE_OK" || { echo "G6_FAILED"; exit 1; }
