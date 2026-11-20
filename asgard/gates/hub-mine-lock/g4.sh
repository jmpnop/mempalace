# G4: MCP stdio proxy end-to-end: initialize, add probe drawer, find it, delete it; repair-status OK
M="$(cd "$(dirname "$0")" && pwd)/mp_call.py"
a=$(timeout 150 python3 -I $M mempalace_add_drawer '{"wing":"zz-hub-probe","room":"probe","added_by":"asgard-gates","content":"g4 hub probe 7f3c9e kiwi-saffron"}'); echo "$a" | head -3
id=$(echo "$a" | sed -n 's/.*"drawer_id": "\([^"]*\)".*/\1/p' | head -1)
s=$(timeout 150 python3 -I $M mempalace_search '{"query":"g4 hub probe 7f3c9e kiwi-saffron","wing":"zz-hub-probe","limit":3}')
d=$(timeout 150 python3 -I $M mempalace_delete_drawer "{\"drawer_id\":\"$id\"}")
r=$(timeout 300 ~/.local/bin/mempalace repair-status 2>&1 | grep -c 'status: *OK')
echo "id=$id found=$(echo "$s" | grep -c "$id") deleted=$(echo "$d" | grep -c '"success": true') repair_ok_sections=$r"
[ -n "$id" ] && echo "$s" | grep -q "$id" && echo "$d" | grep -q '"success": true' && [ "$r" -ge 2 ] && echo G4_OK || echo G4_FAIL
