# Logseq DB EDN → read-only mobile JSON

The **SQLite snapshot** (`data/<GraphName>/db.sqlite`) and any assets are the restorable backup. The corresponding `data/mobile/<graph-id>.json` is a **read-only viewer export**, not a restorable database. The [README](../README.md) describes installing the CLI and running backups in your **private** Git repository.

## Supported export formats

- Desktop Logseq EDN in the **`:datoms`** shape.
- Published npm **`@logseq/cli`** Build EDN using **`:pages-and-blocks`** and **`:classes`**.

`cli_edn_export.py` detects the installed CLI command style using `logseq --help` (or `mobile_export.cli_style: "modern"` / `"legacy"`). Both CLI styles are pointed at an **isolated staged SQLite snapshot**, not the original graph.

The converter produces `schemaVersion`, `source`, `graph`, `pageCount`, `blockCount`, `excludedSystemViewBlocks`, `logseqSchema`, and `pages`, including nested blocks and tags/classes. It excludes internal built-in views. A mobile reader can use the resulting JSON but must not write changes back into the Logseq DB.

**UUID-only pages:** Some legacy CLI exports include non-journal page records with a valid `:block/uuid` but no `:block/title` or `:build/journal`. The converter preserves these records and their nested blocks with a generated `Untitled page (<UUID prefix>)` display title, retaining the supplied page UUID. Records lacking a title, journal date, or valid UUID still fail validation. This fallback does **not** resolve repeated page UUIDs elsewhere in the export; mobile readers should retain duplicate-identity validation.

**Important:** Some older CLI exports omit original block UUIDs and explicit structured references. The converter therefore creates **synthetic identifiers** based on page keys and block positions; IDs may change when content is reordered. They must not be used as stable Logseq editing IDs, and review-progress features should account for potential ID changes. Inline links are preserved as text, but CLI Build EDN may not include structured `references`.

## Export and verify

Install the CLI if needed:

```bash
npm install -g @logseq/cli
logseq --help
```

Quit Logseq and run the standard pipeline:

```bash
bash scripts/backup.sh
python3 -m json.tool data/mobile/manifest.json
```

If the CLI output does not match a supported EDN shape, the converter fails and **preserves previously validated backups**. Do not disable required JSON validation just to force a successful run.

To convert a previously exported EDN file manually (only when needed):

```bash
python3 scripts/edn_to_json.py --input /path/to/graph.edn \
  --graph "Example" --output /tmp/example.json
```

This manual conversion does **not** take a new SQLite snapshot. It uses the supplied EDN file. The temporary EDN used by the automated exporter is discarded after conversion. Do not commit EDN files or JSON with personal notes into the public blueprint repository.

Run regression tests with:

```bash
python3 -m unittest discover -s tests -v
```

Real DB schemas and CLI versions can evolve, so validate the latest JSON against actual notes before relying on automated mobile synchronization.
