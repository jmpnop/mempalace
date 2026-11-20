MP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/mp_call.py"; LOG=~/.config/mempalace/hub.log
mark() { wc -l < $LOG | tr -d ' '; }
opens_since() { tail -n +$1 $LOG | grep -c 'HNSW mtime gap .*7bc6cc44'; }
save() { out=$(timeout 300 python3 -I $MP mempalace_add_drawer "{\"wing\":\"zz-hub-probe\",\"room\":\"probe\",\"added_by\":\"asgard-gates\",\"content\":\"single-cache gate $1 $(date +%s%N) persimmon\"}"); LAST_ID=$(echo "$out" | sed -n 's/.*"drawer_id": "\([^"]*\)".*/\1/p' | head -1); echo "$LAST_ID" >> $PROBES; echo "$out" | sed -n 's/^mempalace_add_drawer \([0-9.]*\)s.*/\1/p'; }
readback() { timeout 300 python3 -I $MP mempalace_get_drawer "{\"drawer_id\":\"$1\"}" | grep -c "\"$1\""; }
search() { timeout 300 python3 -I $MP mempalace_search '{"query":"single-cache gate persimmon","wing":"zz-hub-probe","limit":3}' | sed -n 's/^mempalace_search \([0-9.]*\)s.*/\1/p'; }
cleanup() { for id in $(sort -u $PROBES); do [ -n "$id" ] && timeout 300 python3 -I $MP mempalace_delete_drawer "{\"drawer_id\":\"$id\"}" >/dev/null; done; : > $PROBES; }
le() { awk -v a="$1" -v b="$2" 'BEGIN{exit !(a+0<=b+0 && a!="")}'; }
