cd "$(dirname "$0")"; PROBES=$PWD/probes.txt; . ./lib.sh; : > $PROBES
ts=""; ss=""; ok=1
for k in 1 2 3; do t=$(save q3-$k); ts="$ts ${t:-ERR}"; le "$t" 6 || ok=0; s=$(search); ss="$ss ${s:-ERR}"; done
t=$(save q3-4); ts="$ts ${t:-ERR}"; le "$t" 6 || ok=0; cleanup
echo "saves:$ts | searches:$ss"; [ $ok = 1 ] && echo Q3_OK || echo Q3_FAIL
