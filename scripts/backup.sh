#!/usr/bin/env bash
# Manually runs one safe SQLite snapshot, optional CLI JSON export, then Git commit/push.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f config.json ]]; then
  echo '[error] Missing config.json; run: cp config.example.json config.json' >&2
  exit 1
fi

PYTHON="${PYTHON:-python3}"
# Lock the whole operation, including Git commit/push, without requiring flock(1).
if [[ "${LOGSEQ_BACKUP_LOCK_HELD:-0}" != '1' ]]; then
  exec "$PYTHON" -S "$ROOT/scripts/with_lock.py" "$ROOT/.logseq-backup.lock" bash "$ROOT/scripts/backup.sh"
fi
# Validate Git before doing potentially expensive snapshotting.
if ! git rev-parse --show-toplevel >/dev/null 2>&1; then
  echo '[error] Git repository not initialized. Run: git init -b main' >&2
  exit 1
fi

"$PYTHON" -S scripts/snapshot.py --config "$ROOT/config.json"

# Read only the git-specific settings; never stage config.json.
readarray_safe="$("$PYTHON" -S - <<'PY'
import json
with open('config.json', encoding='utf8') as file: cfg=json.load(file)
g=cfg.get('git', {})
print('1' if g.get('enabled',True) else '0')
print('1' if g.get('push',False) else '0')
print(g.get('remote','origin'))
print(g.get('branch','main'))
PY
)"
while IFS= read -r line; do
  case "${_line_index:-0}" in
    0) ENABLED="$line";;
    1) PUSH="$line";;
    2) REMOTE="$line";;
    3) BRANCH="$line";;
  esac
  _line_index="$(( ${_line_index:-0} + 1 ))"
done <<< "$readarray_safe"

if [[ "$ENABLED" != '1' ]]; then
  echo '[ok] Backup generated. Git integration disabled in config.'
  exit 0
fi

# Stage ONLY completed backup data; never auto-commit scripts/config/secrets.
git add -A -- data/
if git diff --cached --quiet; then
  echo '[ok] No backup data changes; nothing to commit.'
else
  git commit -m "backup: update Logseq snapshot"
  echo '[ok] Latest backup committed locally.'
fi

if [[ "$PUSH" == '1' ]]; then
  if ! git remote get-url "$REMOTE" >/dev/null 2>&1; then
    echo "[error] Git remote '$REMOTE' not configured; local commit is safe." >&2
    exit 1
  fi
  git push "$REMOTE" "$BRANCH"
  echo "[ok] Pushed to $REMOTE/$BRANCH"
else
  echo '[info] Git push disabled. Set git.push=true in config.json after adding a PRIVATE remote.'
fi
