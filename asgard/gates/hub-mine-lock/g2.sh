# G2: throwaway-palace mine with a 5 ms GIL watchdog; no hold >250 ms inside chromadb writes
T=$(mktemp -d "${TMPDIR:-/tmp}/g2palace.XXXX"); mkdir -p $T/cfg $T/palace
out=$(MEMPALACE_CONFIG_DIR=$T/cfg MEMPALACE_PALACE_PATH=$T/palace timeout 900 ~/.local/share/uv/tools/mempalace/bin/python "$(cd "$(dirname "$0")" && pwd)/gilwatch.py" $T/palace g2bench 60 2>&1)
echo "$out" | tail -14
files=$(echo "$out" | awk '/Files processed:/{print $3}')
bad=$(echo "$out" | awk '/ x  sum /{ if ($0 ~ /rust.py|Collection.py:5[0-9][0-9]:(upsert|add)/ && $6+0 > 0.25) n++ } END{print n+0}')
[ "${files:-0}" -ge 60 ] && [ "$bad" = 0 ] && echo G2_OK || echo "G2_FAIL files=$files chroma_holds_over_250ms=$bad"
