cd "$(dirname "$0")"; PROBES=$PWD/probes.txt; . ./lib.sh; : > $PROBES
m=$(mark); ts=""; ok=1
for k in 1 2 3 4 5; do t=$(save q2-$k); ts="$ts ${t:-ERR}"; le "$t" 6 || ok=0; done
o=$(opens_since $m); cleanup
echo "saves:$ts | client opens: $o"; [ $ok = 1 ] && [ $o -le 1 ] && echo Q2_OK || echo Q2_FAIL
