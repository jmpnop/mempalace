# Q1: in-process gate on a fresh throwaway palace under /private/tmp (never the live palace).
# The MCP server redirects stdout to stderr, so both streams are captured and the verdict is read from them.
cd "$(dirname "$0")"; T=$(mktemp -d /private/tmp/q1-single-cache.XXXXXX); mkdir -p $T/cfg $T/palace
MEMPALACE_CONFIG_DIR=$T/cfg MEMPALACE_PALACE_PATH=$T/palace timeout 900 ~/.local/share/uv/tools/mempalace/bin/python -I ./q1_inproc.py $T > $T/run.log 2>&1
cat $T/run.log | grep -E '^(ok|FAIL) ' ; cat $T/run.log | grep -E '^Q1_(OK|FAIL)' | tail -1
