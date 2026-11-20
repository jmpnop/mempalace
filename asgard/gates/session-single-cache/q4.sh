cd "$(dirname "$0")"; PROBES=$PWD/probes.txt; . ./lib.sh; : > $PROBES
save q4-warm >/dev/null; search >/dev/null
touch -m ~/.config/mempalace/palace/chroma.sqlite3; m=$(mark)
st=$(search); o=$(opens_since $m)
t=$(save q4-after); id=$LAST_ID; rb=$(readback $id); cleanup
echo "search after external touch ${st}s, client opens: $o | save ${t}s, read back: $rb"
[ "$o" -ge 1 ] && [ "$rb" -ge 1 ] && echo Q4_OK || echo Q4_FAIL
