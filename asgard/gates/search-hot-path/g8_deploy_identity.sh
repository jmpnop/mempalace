#!/usr/bin/env bash
# G8: the installed package tree equals the fork commit's mempalace/ (no uncommitted changes, commit
# pushed), the chromadb bindings are the patched build, and the hub answers /healthz.
source "$(dirname "$0")/lib.sh"
SP="${MEMPALACE_TOOL_SITE:-$HOME/.local/share/uv/tools/mempalace/lib/python3.13/site-packages}"
PATCHED_SHA=2fedfa9949159c30dea086f8ac3c18e9c17d14a8448d8eaa98bbd2e7ffc65e20
diffs="$(diff -rq -x __pycache__ "$REPO/mempalace" "$SP/mempalace" | wc -l | tr -d ' ')"
dirty="$(git -C "$REPO" status --porcelain -- mempalace | wc -l | tr -d ' ')"
head="$(git -C "$REPO" rev-parse HEAD)"
git -C "$REPO" fetch -q origin
pushed="$(git -C "$REPO" branch -r --contains "$head" | wc -l | tr -d ' ')"
sha="$(shasum -a 256 "$SP"/chromadb_rust_bindings/*.so | cut -c1-64)"
hz="$(curl -s -m 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/healthz || true)"
echo "fork $(git -C "$REPO" rev-parse --short HEAD): installed-vs-fork differing files $diffs, uncommitted $dirty, on a remote branch $pushed"
echo "bindings ${sha:0:16}… (patched ${PATCHED_SHA:0:16}…), healthz $hz"
[ "$diffs" = 0 ] && [ "$dirty" = 0 ] && [ "$pushed" -ge 1 ] && [ "$sha" = "$PATCHED_SHA" ] && [ "$hz" = 200 ] \
  && echo G8_DEPLOY_IDENTICAL || { echo G8_FAILED; exit 1; }
