cd "$(dirname "$0")"; LOG=~/.config/mempalace/hub.log; start=$(wc -l < $LOG | tr -d ' ')
python3 -I ./p2_live.py > ./p2-run.out 2>&1
summary=$(cat ./p2-run.out | grep '^mine rc=' | tail -1); echo "$summary"
waits=$(tail -n +$start $LOG | sed -n 's/.*CALL mempalace_search read wait=\([0-9]*\)ms.*/\1/p')
n=$(echo "$waits" | grep -c .); max=$(echo "$waits" | sort -n | tail -1)
mine=$(tail -n +$start $LOG | grep -E 'CALL mempalace_mine write' | tail -1 | cut -c1-120)
echo "searches logged by the hub: $n | worst lock wait: ${max:-?} ms | $mine"
case "$summary" in *"mine rc=0"*) mok=1;; *) mok=0;; esac
[ $mok = 1 ] && [ "$n" -ge 3 ] && [ "${max:-999999}" -le 60000 ] && echo P2_OK || echo P2_FAIL
