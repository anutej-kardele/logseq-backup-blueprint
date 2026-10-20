# Backing up while Logseq DB is running

## Why the live backup times out

Logseq's DB version holds an **exclusive SQLite lock** on the graph's primary
`db.sqlite`. A separate Python process may not open it while Logseq is running.
This is a Logseq behavior, not evidence of database corruption.

This update offers a safe fallback to Logseq's **own completed local backups**,
which the Logseq worker can create while the application is open.

## Setup

In your local-only `config.json`, retain your other configuration and change the
`snapshot` section to:

```json
"snapshot": {
  "include_assets": true,
  "max_file_mib": 95,
  "timeout_seconds": 8,
  "fallback_to_native_backup": true,
  "native_backup_max_age_minutes": 120
}
```

Run `bash scripts/backup.sh` with Logseq **open** and **closed**. Keep
`git.push=false` while testing. With Logseq open, the script:

1. Attempts the normal consistent snapshot for up to eight seconds.
2. If SQLite is locked, looks for a Logseq-created `.sqlite` file inside
   `<graph>/backup/` or `<graph>/backups/`.
3. Rejects snapshots older than 120 minutes, or older than the database
   currently published in `data/Activity/` (conservative protection against
   regressing the backup).
4. Verifies a standalone SQLite copy with `PRAGMA integrity_check`.
5. Generates JSON from **that exact snapshot** with the installed Logseq CLI.
6. Publishes both files only when conversion and validation succeed.

The command explicitly reports whether it backed up the **live** database or
used a **Logseq native backup**. In native fallback mode, your cloud backup can
lag your newest edits by up to the age shown in the console. This does not
provide real-time mobile sync.

If no qualifying native backup exists, the script reports an error, preserves
the current `data/Activity/db.sqlite` and `data/mobile/activity.json`, and does
not push a stale snapshot as if it were new. Wait for Logseq to generate another
native backup, or quit Logseq and retry the command to back up the current data.

## Inspect Logseq's native backups

```bash
find "$HOME/logseq/graphs/Activity/backup" -maxdepth 3 -type f \
  \( -name 'db.sqlite' -o -name '*.sqlite' \) -print
```

Your Logseq version may instead use `backups/`. The script checks both.

## Validation and Git

```bash
python3 -m unittest discover -s tests -v
bash scripts/backup.sh
sqlite3 data/Activity/db.sqlite 'PRAGMA integrity_check;'
git status
```

If tests succeed, commit the code update (including previously uncommitted
`snapshot_worker.py` from the earlier automation release) and push:

```bash
git add scripts/snapshot.py scripts/snapshot_worker.py \
  config.example.json tests/test_automation.py docs/LIVE_LOGSEQ_BACKUPS.md
git commit -m "fix: back up Logseq from native snapshots when DB is locked"
git push origin master
```

Keep `config.json` ignored by Git. A private GitHub repo is access-controlled
but does not encrypt your notes end-to-end.

## Limitations

- The script does not modify or stop Logseq. It cannot force an immediate fresh
  backup from a running instance with an exclusive SQLite lock.
- Logseq's built-in backups are normally made periodically; the fallback may
  lag behind recently typed notes.
- This update does not install a macOS `launchd` schedule. Verify manual runs
  in both states before enabling scheduled backups.
- Graph attachments are copied from `assets/` and may change independently of
  the database; a strict full-graph point-in-time snapshot is not guaranteed
  if attachments are being modified simultaneously.
- If the native backup folder has a different structure, inspect its contents
  before expanding the discovery logic.
