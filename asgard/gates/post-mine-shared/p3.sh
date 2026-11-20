cd "$(dirname "$0")"; MP=$PWD/mp_call.py; n=0
while IFS= read -r f; do timeout 300 python3 -I $MP mempalace_delete_by_source "$(python3 -c 'import json,sys;print(json.dumps({"source_file":sys.argv[1],"dry_run":False}))' "$f")" >/dev/null && n=$((n+1)); done < <(find "$PWD/p2src" -type f 2>/dev/null)
for k in $(seq 1 10); do id=$(timeout 300 python3 -I $MP mempalace_list_drawers '{"wing":"zz-hub-probe","limit":1}' | sed -n 's/.*"drawer_id": "\(drawer_zz-hub-probe_probe_[^"]*\)".*/\1/p' | head -1); [ -z "$id" ] && break; timeout 300 python3 -I $MP mempalace_delete_drawer "{\"drawer_id\":\"$id\"}" >/dev/null; done
left=$(timeout 300 python3 -I $MP mempalace_list_drawers '{"wing":"zz-hub-probe","limit":1}' | sed -n 's/.*"total": \([0-9]*\).*/\1/p' | head -1)
ok=$(timeout 300 ~/.local/bin/mempalace repair-status 2>&1 | grep -c 'status: *OK')
echo "sources cleaned=$n | probe-wing drawers left=${left:-?} | repair OK sections=$ok"; [ "${left:-x}" = 0 ] && [ "$ok" -ge 2 ] && echo P3_OK || echo P3_FAIL
