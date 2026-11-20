# G3: hub stays responsive (/healthz <=2 s, every probe) for 3 min while a throwaway-wing mine writes through it
W=$(cd "$(dirname "$0")" && pwd); C=$W/g3src; rm -rf "$C"; mkdir -p "$C"
( cd "${MINE_SRC:?set MINE_SRC to a git checkout to copy test files from}" && git ls-files 2>/dev/null | awk '/\.(py|md|ts|rs|txt|json)$/' | head -80 | while read f; do mkdir -p "$C/$(dirname "$f")"; cp "$f" "$C/$f"; done )
n0=$(ls -R "$C" | wc -l)
timeout 600 ~/.local/bin/mempalace mine "$C" --mode projects --wing zz-hub-probe --agent asgard-gates > $W/g3-mine.log 2>&1 &
MP=$!; sleep 5; worst=0; slow=0; n=0
end=$(( $(date +%s) + 180 ))
while [ $(date +%s) -lt $end ]; do
  t=$(curl -s -m 30 -o /dev/null -w '%{time_total}' http://127.0.0.1:8765/healthz); n=$((n+1))
  awk -v t="$t" 'BEGIN{exit !(t>2)}' && slow=$((slow+1))
  worst=$(awk -v a="$worst" -v b="$t" 'BEGIN{print (b>a)?b:a}'); sleep 1
done
alive=$(kill -0 $MP 2>/dev/null && echo yes || echo no); wait $MP; rc=$?
filed=$(cat $W/g3-mine.log | awk '/Drawers filed:/{print $3}')
echo "probes=$n slow(>2s)=$slow worst=${worst}s mine_still_running_at_3min=$alive mine_rc=$rc filed=${filed:-?}"
[ $n -ge 100 ] && [ $slow = 0 ] && [ "${filed:-0}" -gt 0 ] && echo G3_OK || echo G3_FAIL
