cd "$(dirname "$0")"; PROBES=$PWD/probes.txt; . ./lib.sh; : > $PROBES
cold=$(save u2-cold); ts=""; ok=1
for k in 1 2 3 4; do t=$(save u2-$k); ts="$ts ${t:-ERR}"; le "$t" 6 || ok=0; done
ss=""; for k in 1 2; do s=$(search); ss="$ss ${s:-ERR}"; t=$(save u2-i$k); ts="$ts ${t:-ERR}"; le "$t" 6 || ok=0; done
cleanup; echo "cold save ${cold}s | warm saves:$ts | searches:$ss"; [ $ok = 1 ] && echo U2_OK || echo U2_FAIL
