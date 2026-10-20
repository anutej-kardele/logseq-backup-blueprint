# Multi-graph backup and mobile manifest

Use the **[README](../README.md)** to install the project into **your own private Git repository**. This document describes the current multi-graph behavior; no separate patch or old update ZIP is needed.

## Configure graph discovery

Copy `config.example.json` to the ignored local `config.json` and customize the source root:

```json
{
  "source_graphs_dir": "~/logseq/graphs",
  "auto_discover_graphs": true,
  "exclude_graphs": []
}
```

This excerpt shows the graph options only; **retain the `snapshot`, `mobile_export`, and `git` sections** from `config.example.json`. Graphs are immediate child folders containing `db.sqlite`. Non-graph folders, hidden directories, and symlinks are ignored. Use `exclude_graphs` to skip specific names. Conflicting safe IDs are rejected rather than silently overwriting a graph.

For multi-graph mode, configure `mobile_export.enabled: true` and `mobile_export.required: true`. Do not push until local validation succeeds. An older configuration with `source_graph_dir` and `graph_name` still runs in legacy single-graph mode.

## Output contract (schemaVersion 1)

With graphs named `Activity` and `Demo`, example output is:

```text
data/
├── Activity/db.sqlite
├── Demo/db.sqlite
└── mobile/
    ├── activity.json
    ├── demo.json
    └── manifest.json
```

The names are examples, not built-in requirements. JSON filenames are derived from safe graph IDs; the SQLite graph folder keeps its original graph name.

Example `data/mobile/manifest.json` (hashes and counts are illustrative):

```json
{
  "schemaVersion": 1,
  "graphs": [
    {
      "id": "activity",
      "name": "Activity",
      "file": "activity.json",
      "path": "data/mobile/activity.json",
      "pageCount": 31,
      "blockCount": 155,
      "sha256": "<sha256-hex-string>"
    },
    {
      "id": "demo",
      "name": "Demo",
      "file": "demo.json",
      "path": "data/mobile/demo.json",
      "pageCount": 2,
      "blockCount": 5,
      "sha256": "<sha256-hex-string>"
    }
  ]
}
```

In actual exports, `sha256` is a **64-character hexadecimal SHA-256** of the complete corresponding JSON file. `file` is relative to `data/mobile/` and `path` is relative to the repository root. A reader should fetch **the manifest and graph JSON from the same Git commit SHA** and verify each hash to avoid mixing generations. The manifest is not independently signed.

## Validation and failure behavior

- SQLite snapshots are made from the source graph **without writing to it**, with a timeout. Export uses an isolated staged snapshot, not the live graph.
- Every discovered graph's snapshot and JSON must validate before any new graph files or manifest are published. Normal filesystem publication errors trigger rollback. A sudden power loss is **not** a guaranteed multi-file atomic transaction.
- `manifest.json` is published last. Graphs removed from the source are omitted from the new manifest but their old backup files are **not** automatically deleted.
- No valid graphs, a failed CLI export, a stale/locked graph without a safe fallback, or colliding graph IDs will stop publication.
- `.logseq-backup.lock` prevents concurrent runs. The lock is ignored by Git.

## Test in a private repository

Quit Logseq (⌘Q), then run:

```bash
python3 -m unittest discover -s tests -v
bash scripts/backup.sh
python3 -m json.tool data/mobile/manifest.json
find data -name db.sqlite -print
```

Optionally run `sqlite3 data/GRAPH_NAME/db.sqlite 'PRAGMA integrity_check;'` on each output (use your real graph folder name; result should be `ok`). Check a recently edited note in JSON, confirm your Git remote points to a **PRIVATE repository**, then set `git.push: true` in local `config.json`.

Keep the data repository private: even deleted notes remain in historical commits. See [live backup caveats](LIVE_LOGSEQ_BACKUPS.md) and [EDN format notes](EDN_EXPORT.md).
