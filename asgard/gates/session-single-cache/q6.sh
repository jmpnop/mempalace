F="$(cd "$(dirname "$0")/../hub-mine-lock" && pwd)"
u=$(cd $F && ~/.local/share/uv/tools/mempalace/bin/python -I ./g5_unit.py | tail -1); l=$(cd $F && bash ./g6_live.sh 2>&1 | tail -2 | tr '\n' ' '); echo "unit: $u | live: $l"
case "$u$l" in *G5_OK*G6_OK*) echo Q6_OK;; *) echo Q6_FAIL;; esac
