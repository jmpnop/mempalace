cd "$(dirname "$0")"; PROBES=$PWD/probes.txt; . ./lib.sh; : > $PROBES
SRC=$PWD/q5src; rm -rf $SRC; mkdir -p $SRC
( cd "${MINE_SRC:?set MINE_SRC to a git checkout to copy test files from}" && git ls-files | awk '/\.md$/' | head -5 | while read f; do mkdir -p "$SRC/$(dirname "$f")"; cp "$f" "$SRC/$f"; done )
timeout 1800 ~/.local/bin/mempalace mine "$SRC" --mode projects --wing zz-hub-probe --agent asgard-gates > $PWD/q5-mine.log 2>&1; mrc=$?
filed=$(cat $PWD/q5-mine.log | sed -n 's/.*Drawers filed: *\([0-9]*\).*/\1/p' | head -1)
t=$(save q5-after-mine); id=$LAST_ID; rb=$(readback $id)
found=$(timeout 300 python3 -I $MP mempalace_search '{"query":"single-cache gate q5-after-mine persimmon","wing":"zz-hub-probe","limit":5}' | grep -c "$id")
cleanup
n=0; while IFS= read -r f; do timeout 300 python3 -I $MP mempalace_delete_by_source "$(python3 -c 'import json,sys;print(json.dumps({"source_file":sys.argv[1],"dry_run":False}))' "$f")" >/dev/null && n=$((n+1)); done < <(find "$SRC" -type f)
left=$(timeout 300 python3 -I $MP mempalace_list_drawers '{"wing":"zz-hub-probe","limit":1}' | sed -n 's/.*"total": \([0-9]*\).*/\1/p' | head -1)
rok=$(timeout 300 ~/.local/bin/mempalace repair-status 2>&1 | grep -c 'status: *OK')
echo "mine rc=$mrc filed=${filed:-?} | save ${t}s read back=$rb found by search=$found | cleaned sources=$n left=${left:-?} | repair OK sections=$rok"
[ $mrc = 0 ] && [ "${filed:-0}" -gt 0 ] && [ "$rb" -ge 1 ] && [ "$found" -ge 1 ] && [ "${left:-x}" = 0 ] && [ "$rok" -ge 2 ] && echo Q5_OK || echo Q5_FAIL
