# Logseq DB EDN → Read-only JSON

The authoritative disaster recovery backup is `data/Activity/db.sqlite` (and graph assets). `data/mobile/activity.json` is a *read-only companion export*, not a restorable Logseq database.

## Supported EDN formats

- Logseq desktop **`:datoms`** export, tested with desktop DB schema 65.34.
- Published `@logseq/cli` **`:classes` / `:pages-and-blocks`** Build EDN export, tested against an actual CLI export at DB schema 65.22 (and synthetic fixture tests).

The converter automatically recognizes the two shapes. Both produce the same top-level mobile JSON keys (`schemaVersion`, `source`, `logseqSchema`, `graph`, `pageCount`, `blockCount`, `excludedSystemViewBlocks`, `pages`) and the same general page/block object structure. The `source` field identifies which format was used.

The CLI Build EDN format frequently omits original block UUIDs, numeric DataScript IDs, and explicit page references. The converter creates deterministic *synthetic* UUIDs and negative `entityId` values for read-only navigation. These **must not** be used as Logseq IDs for editing or write-back. Synthetic identifiers use page keys and block tree positions, so inserting/reordering notes can change block IDs. Link text such as `[[Page]]` is preserved inside content, but `references` is empty for CLI Build EDN when not explicitly provided. Some advanced Logseq views, embedded content, and custom property semantics may need future viewer support.

Built-in internal pages/views are excluded from user notes. Journal days are emitted as `YYYY-MM-DD` titles; tag/class pages and nested blocks are preserved. Both formats can contain sensitive notes—keep your repository private.

## Automated export (recommended)

Install the CLI once on your MacBook:

```bash
node --version
npm install -g @logseq/cli
logseq --help
```

Your existing ignored `config.json` should contain:

```json
{
  "source_graph_dir": "~/logseq/graphs/Activity",
  "graph_name": "Activity",
  "snapshot": {"include_assets": true, "max_file_mib": 95, "timeout_seconds": 30},
  "mobile_export": {
    "enabled": true,
    "mode": "edn-cli",
    "cli_command": ["logseq"],
    "cli_style": "auto",
    "timeout_seconds": 120,
    "required": true
  },
  "git": {"enabled": true, "push": false, "remote": "origin", "branch": "master"}
}
```

Then run:

```bash
bash scripts/backup.sh
```

This creates a validated SQLite snapshot, exports Build EDN from the staged snapshot (never from your live graph), converts it, and updates the latest files *only when conversion succeeds*. The temporary EDN is removed afterwards. Only after inspecting the JSON and confirming everything works should you set `git.push=true` for off-device automatic pushing.

A sample *real* `@logseq/cli` export contained 24 journal pages, 7 user classes, 155 content blocks, and 6 internal view blocks. Counts can differ from a desktop export created at another time or schema revision. Check your actual notes rather than expecting a fixed block count.

## Manual export (optional)

You can still convert either EDN export on its own:

```bash
python3 scripts/edn_to_json.py --input "$HOME/Downloads/activity-cli.edn" --graph Activity
```

The manual command reads the EDN file you name; it does not fetch a fresh file. The automated `backup.sh` path exports a new one on every successful run.

Run regression tests with `python3 -m unittest discover -s tests -v`. Tests contain synthetic notes only. The user's original EDN/database/JSON and their local `config.json` are not included with updates.
