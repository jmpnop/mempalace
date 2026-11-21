# Shared setup for the search-hot-path gate scripts. Source it; do not run it.
#
#   REPO     the fork checkout (this file's repository)
#   PY       the fork's .venv interpreter, used for BASE and FORK alike
#   BASE     a git worktree of the deployed commit, created on first use
#   FORK     the checkout itself
#   WORK     scratch root for working copies (default: $TMPDIR/search-hot-path-gates)
#
# Every comparison runs each side on its own APFS clone (cp -c) of a frozen
# palace and checks afterwards that the frozen palace is unchanged.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PY="$REPO/.venv/bin/python"
BASE_COMMIT="ef89c2cb"
BASE="$REPO/.worktrees/base-$BASE_COMMIT"
FORK="$REPO"
GATES="$REPO/asgard/gates/search-hot-path"
WORK="${WORK:-${TMPDIR:-/tmp}/search-hot-path-gates}"
mkdir -p "$WORK"

die() { echo "$*" >&2; exit 1; }

ensure_base() {
  if [ ! -d "$BASE/mempalace" ]; then
    git -C "$REPO" worktree add --detach "$BASE" "$BASE_COMMIT" >/dev/null 2>&1 \
      || die "GATE_SETUP_FAILED cannot create the BASE worktree at $BASE_COMMIT"
    grep -qx ".worktrees/" "$REPO/.git/info/exclude" 2>/dev/null \
      || echo ".worktrees/" >> "$REPO/.git/info/exclude"
  fi
  [ "$(git -C "$BASE" rev-parse --short=8 HEAD)" = "$BASE_COMMIT" ] \
    || die "GATE_SETUP_FAILED BASE worktree is not at $BASE_COMMIT"
}

# fingerprint DIR -> one line: sha256 over (relative path, inode, mtime_ns, size) of every file
fingerprint() {
  "$PY" -I - "$1" <<'EOF'
import hashlib, os, sys
root = sys.argv[1]
h = hashlib.sha256()
for d, dirs, files in sorted(os.walk(root)):
    dirs.sort()
    for name in sorted(files):
        p = os.path.join(d, name)
        st = os.lstat(p)
        h.update(f"{os.path.relpath(p, root)}\0{st.st_ino}\0{st.st_mtime_ns}\0{st.st_size}\n".encode())
print(h.hexdigest())
EOF
}

# working_copy FROZEN NAME -> path of a fresh writable APFS clone under $WORK
working_copy() {
  local dst="$WORK/copies/$2"
  if [ -e "$dst" ]; then chmod -R u+w "$dst"; rm -rf "$dst"; fi
  mkdir -p "$WORK/copies"
  cp -cR "$1" "$dst"
  chmod -R u+w "$dst"
  echo "$dst"
}

# side NAME -> tree path
tree_of() { case "$1" in base) echo "$BASE" ;; fork) echo "$FORK" ;; *) die "bad side $1" ;; esac; }

# run_side SIDE MODE PALACE OUT [harness args...]
run_side() {
  local side="$1" mode="$2" palace="$3" out="$4"; shift 4
  local scratch="$WORK/scratch/$side-$mode-$$-$RANDOM"
  mkdir -p "$scratch"
  "$PY" -I "$GATES/harness.py" "$mode" --tree "$(tree_of "$side")" --palace "$palace" \
    --scratch "$scratch" "$@" > "$out"
}
