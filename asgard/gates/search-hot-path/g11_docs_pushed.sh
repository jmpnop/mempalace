#!/usr/bin/env bash
# G11: doc 08 and its README index row are committed and pushed, and everything this work pushed
# (diffs and commit messages since the deployed commit) is clean of personal data and AI mentions.
# The word list is private and lives outside the repository: SCAN_WORDS_FILE, one word per line.
source "$(dirname "$0")/lib.sh"
WORDS="${SCAN_WORDS_FILE:-$HOME/.config/mempalace/local-patches/scan-words.txt}"
[ -s "$WORDS" ] || die "G11_FAILED no scan word list at SCAN_WORDS_FILE"
cd "$REPO"
ok=1
doc=asgard/docs/08-search-hot-path.md
git ls-files --error-unmatch "$doc" >/dev/null 2>&1 || { echo "doc 08 not committed"; ok=0; }
git show HEAD:asgard/README.md | grep -q "08-search-hot-path.md" || { echo "README has no row for doc 08"; ok=0; }
[ -z "$(git status --porcelain -- asgard mempalace tests)" ] || { echo "uncommitted changes"; git status --short | head; ok=0; }
git fetch -q origin
[ "$(git rev-parse HEAD)" = "$(git rev-parse '@{upstream}')" ] || { echo "HEAD is not pushed"; ok=0; }
hits=0
while IFS= read -r w; do
  [ -n "$w" ] || continue
  n="$( { git diff "$BASE_COMMIT"..HEAD | grep '^+' ; git log --format=%B "$BASE_COMMIT"..HEAD; } | grep -ciF -- "$w" || true)"
  if [ "$n" != 0 ]; then echo "scan hit: a listed word appears $n time(s)"; hits=$((hits + n)); fi
done < "$WORDS"
echo "scanned $(wc -l < "$WORDS" | tr -d ' ') words over $(git rev-list --count "$BASE_COMMIT"..HEAD) commits: $hits hits"
[ "$hits" = 0 ] || ok=0
[ "$ok" = 1 ] && echo G11_DOCS_PUSHED || { echo G11_FAILED; exit 1; }
