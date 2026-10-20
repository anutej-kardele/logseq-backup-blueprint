# SQLite backups while Logseq is running

## Why the source can be locked

Some Logseq DB versions hold an exclusive lock on the graph's `db.sqlite`. A separate Python process may be unable to read it until Logseq quits. This is **not evidence of database corruption**. The safest way to capture the latest notes is to quit Logseq with **⌘Q** before running `bash scripts/backup.sh`.

## Optional Logseq-native fallback

The example local `config.json` enables:

```json
"snapshot": {
  "include_assets": true,
  "max_file_mib": 95,
  "timeout_seconds": 8,
  "fallback_to_native_backup": true,
  "native_backup_max_age_minutes": 120
}
```

Keep this section alongside your other `config.json` settings. On a live SQLite timeout, the backup tool can inspect completed Logseq-created `.sqlite` snapshots in `<graph>/backup/` and `<graph>/backups/`, subject to age and freshness checks. It validates the candidate SQLite copy and exports JSON from **that same staged snapshot**, never from the active database.

A native backup may be up to the configured maximum age and **may omit your latest edits**. The script refuses a native snapshot older than the last published backup. If no sufficiently fresh validated copy exists, it fails without replacing the previous validated database/JSON. For multi-graph backup, failure of one graph stops the whole update.

Do not bypass a SQLite lock by copying a live `db.sqlite` with `cp`, deleting WAL/SHM files, or killing Logseq's database processes.

## Validate a backup

From your **private** repository with Logseq closed:

```bash
bash scripts/backup.sh
python3 -m json.tool data/mobile/manifest.json
find data -name db.sqlite -print
```

For each backed-up graph, run `sqlite3 data/GRAPH_NAME/db.sqlite 'PRAGMA integrity_check;'`, substituting the real graph folder name (expected output: `ok`). To test restore, use a **copy** of the backed-up SQLite file in a **separate test graph**; never overwrite your live graph while testing.

If you run with Logseq open, carefully read the log's source label (`live` versus `logseq-native-backup`) and the native backup age before assuming the backup includes your newest content.

## Limitations and privacy

- Database content is validated; the copied `assets/` directory may not be perfectly synchronized with concurrent attachment changes.
- No multi-file operation is guaranteed crash-atomic during power loss.
- Git preserves prior revisions of notes in history; use only a **private** Git remote, with secure access and storage appropriate to your threat model.
- The backup script does not close/restart Logseq or install a scheduler. macOS automation is an **optional separate shortcut** explained in [AUTOMATION.md](AUTOMATION.md).
