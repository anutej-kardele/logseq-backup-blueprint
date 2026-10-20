# Automated Logseq SQLite + Android JSON backups (macOS)

This project is **local-first**. It never edits your live Logseq graph. Each run:

1. Creates a consistent SQLite snapshot in a temporary directory using a **separate Python subprocess with a hard timeout**.
2. Copies `assets/` if present.
3. Asks a locally installed Logseq CLI to **export EDN from that temporary SQLite snapshot**.
4. Converts the EDN using `edn_to_json.py` into the same `data/mobile/activity.json` format that was already tested with your manual export.
5. Validates all generated files. **Only on success** does it replace the local checkout's previous copies.
6. Commits changed `data/` files and optionally pushes to a PRIVATE GitHub repository.

The source is configured with the ignored `config.json` file. No permanent EDN is required and no GitHub or Logseq passwords are stored in the config.

> **Compatibility boundary:** Logseq's DB-version CLI has two generations in the wild. The adapter supports both documented command syntaxes but has only been integration-tested with simulated CLIs. **You must confirm real EDN export works with your particular installed CLI and Logseq database version before relying on mobile JSON automation.** The exporter now supports both desktop `:datoms` and older npm CLI Build EDN (`:pages-and-blocks` / `:classes`) as seen in an actual CLI export from schema 65.22. If a future CLI produces an unsupported format, the script still fails without replacing the last good backups.

## A. Apply the update

This update ZIP must be extracted **into the root of your existing `logseq-backup` repository**, not into a new folder. It does **not** contain `config.json`, `data/`, your private notes, or a Git repository.

```bash
cd ~/Code/Git/logseq-backup
unzip -o ~/Downloads/logseq-automation-update.zip -d .
```

Run tests:

```bash
python3 -m unittest discover -s tests -v
```

## B. Install a Logseq CLI on your Mac

Check first:

```bash
command -v logseq
logseq --help
```

If `logseq` is missing and you have Node.js 22+, the **published** CLI can be installed with:

```bash
node --version
npm install -g @logseq/cli
logseq --help
```

The published npm package `@logseq/cli` **0.4.x** documents `logseq export-edn -g ... -f ...`. It is older than Logseq's **newer** CLI documented at:

https://github.com/logseq/logseq/blob/master/docs/cli/logseq-cli.md

The newer CLI uses `logseq graph export --type edn --file ... --graph ...`, with an isolated `--root-dir` option. It may be distributed with recent nightly Logseq builds. The Build EDN format produced by this npm CLI is now supported. If future schema changes cause validation failures, do not modify the live DB; inspect the exported EDN before adapting the converter.

The script detects the CLI generation from `logseq --help`. You can override via `mobile_export.cli_style` = `modern` or `legacy`. Both commands always target the *staged* copy, never the original `~/logseq/graphs/Activity`.

## C. Configure for the first safe test

Open your existing `config.json` (it remains ignored by Git). **Keep its `source_graph_dir`, `graph_name`, and your `master` branch.** Change or add these fields:

```json
{
  "source_graph_dir": "~/logseq/graphs/Activity",
  "graph_name": "Activity",
  "snapshot": {
    "include_assets": true,
    "max_file_mib": 95,
    "timeout_seconds": 30
  },
  "mobile_export": {
    "enabled": true,
    "mode": "edn-cli",
    "cli_command": ["logseq"],
    "cli_style": "auto",
    "timeout_seconds": 120,
    "required": true
  },
  "git": {
    "enabled": true,
    "push": false,
    "remote": "origin",
    "branch": "master"
  }
}
```

Set `required: true` so a failed CLI/EDN export **does not publish a fresh database alongside an outdated JSON export**. Keep `push: false` until you have inspected the latest JSON and confirmed that your private GitHub repo is the correct destination.

## D. Run and verify manually

```bash
bash scripts/backup.sh
sqlite3 data/Activity/db.sqlite "PRAGMA integrity_check;"
python3 - <<'PY'
import json
from pathlib import Path
p=Path('data/mobile/activity.json')
j=json.loads(p.read_text())
print('Graph:', j['graph'], 'Pages:', j['pageCount'], 'Blocks:', j['blockCount'])
PY

git status
```

Check that a recent note is in `data/mobile/activity.json` to verify freshness. The expected output resembles:

```text
[1/4] Consistent SQLite backup: ...
[ok] SQLite snapshot validated (... bytes)
[2/4] Exporting read-only mobile JSON (edn-cli)
[info] Logseq CLI export mode: modern   # or legacy
[ok] Mobile JSON: 31 pages / 157 blocks # numbers vary as notes change
[3/4] Publishing latest validated database snapshot
[4/4] Local backup complete
```

The script will make a *local Git commit* if data differs. If it fails, check the terminal error. A failed `mobile_export` with `required: true` keeps the existing backup and JSON intact. A source SQLite lock now fails after the configured timeout, instead of hanging indefinitely.

After confirming both files, set `git.push` to `true` and run again:

```bash
bash scripts/backup.sh
```

It will push any pending committed backup(s) even if the latest snapshot is unchanged.

## E. Unattended scheduling comes *after* testing with Logseq open

The snapshot timeout prevents a forever-hung job; it **does not guarantee** the DB can be read while Logseq holds an exclusive lock. Test `bash scripts/backup.sh` once with Logseq open. If it fails with a lock/timeout, keep scheduled backups disabled and use the Logseq in-app export or arrange backups when Logseq is closed. The backup never forcibly terminates Logseq or removes its lock/WAL files.

Once both states work reliably, use macOS `launchd` for daily invocation. Do not use cron schedules or launchd until that final test passes.

## F. Troubleshooting

- `Logseq CLI executable 'logseq' not found`: install a CLI compatible with your DB version. Existing backup and JSON remain as before.
- `Could not determine Logseq CLI generation`: try `logseq --help`, then set `mobile_export.cli_style` explicitly.
- `Unrecognized Logseq DB EDN export format`: CLI produced a shape other than supported desktop datoms or CLI Build EDN; do not pretend success or publish an outdated JSON.
- `SQLite snapshot timed out after 30s`: some process (often Logseq) holds an exclusive lock. Try after fully quitting Logseq. Your last backed-up files are safe.
- `git push` problems: use your already-working HTTPS authentication; leave `git.push: false` while debugging. Existing local commits are safe.
- DB snapshot works but mobile data is outdated: ensure `mobile_export.enabled: true`, `required: true`, and `mode: "edn-cli"`.

## Security notes

Your `db.sqlite` and `activity.json` contain actual notes. Keep GitHub repository **private** and your `config.json` ignored. A private GitHub repository is not end-to-end encrypted; consider encrypted off-device backups for particularly sensitive information. Git preserves old versions even when you replace a binary file in your working tree. The snapshot remains the recovery source of truth; the JSON is a viewer derivative.

## Verified CLI Build EDN compatibility

The `@logseq/cli` legacy command `logseq export-edn -g SNAPSHOT -f OUTPUT` produced a Build EDN map containing `:classes`, `:pages-and-blocks`, `:properties`, and `:logseq.db.sqlite.export/export-type :graph` on the supplied DB schema 65.22 graph. The parser now supports that shape, retaining nested blocks and user classes/tags. Content block IDs are synthetic when original UUIDs are omitted; they are suitable only for a read-only viewer. The original EDN sample is intentionally excluded from this patch archive.
