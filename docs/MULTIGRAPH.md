# Multi-graph Logseq backups for macOS

This patch updates **only** the Mac backup repository. The Android application
is developed separately and should consume `data/mobile/manifest.json` and the
corresponding JSON files from the *same Git commit/ref*.

## 1. Install without erasing personal files

From `~/Code/Git/logseq-backup`:

```bash
unzip -o ~/Downloads/logseq-multigraph-update.zip -d .
```

The patch **does not contain** `config.json`, databases, JSON exports, or personal
notes. Before committing, review `git diff` for script/config-template changes.

## 2. Change your ignored local config

**Keep your existing** `snapshot`, `mobile_export`, and `git` settings. At the top
of `config.json`, replace the old source settings:

```json
"source_graph_dir": "~/logseq/graphs/Activity",
"graph_name": "Activity",
```

with:

```json
"source_graphs_dir": "~/logseq/graphs",
"auto_discover_graphs": true,
"exclude_graphs": [],
```

Remove the trailing comma only as appropriate in your JSON document. If you
wish to skip a graph, put its exact folder name in `exclude_graphs`, e.g.
`["Demo"]`. File and directory names are generated from the graph folder names.

If you have `mobile_export.enabled: true`, set `required: true` to ensure that
SQLite and mobile JSON always represent the same validated export. You can leave
`git.push: false` for the first test; re-enable it afterward.

**Do not replace your local config with `config.example.json`** unless you intend
to reset other local settings.

## 3. Output contract (v1)

For graphs named `Activity` and `Demo`:

```text
data/
  Activity/db.sqlite
  Demo/db.sqlite
  mobile/
    activity.json
    demo.json
    manifest.json
```

`data/mobile/manifest.json`:

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
      "sha256": "<64-hex-digit-sha256-of-JSON-file>"
    },
    {
      "id": "demo",
      "name": "Demo",
      "file": "demo.json",
      "path": "data/mobile/demo.json",
      "pageCount": 1,
      "blockCount": 2,
      "sha256": "<64-hex-digit-sha256-of-JSON-file>"
    }
  ]
}
```

Counts here are **illustrative**, not claimed to be your Demo graph's actual data.
Use `id` as the stable app identity (relative to the graph folder name).
`file` is relative to `data/mobile/`; `path` is repository-relative.
`sha256` lets a client verify the downloaded JSON bytes. The manifest itself
is not cryptographically signed; trust is based on your authenticated GitHub
connection. Read the manifest and files from the **same commit SHA** to avoid
mixing versions when a new backup push occurs during a download.

`Activity` keeps its old lowercase `activity.json` path. Existing JSON schemas
for individual graphs are unchanged.

## 4. Validation and safe publication

The script discovers graph folders at one level below the source root, ignoring
non-graph folders, hidden folders and symlinks. Graph names that would collide
when converted to safe IDs are rejected; the `mobile` graph name is reserved
because `data/mobile` already exists.

Each graph is independently copied using the existing bounded SQLite snapshot
logic, then passed to the supported Logseq CLI EDN exporter and JSON converter.
Files are size-checked. **Every** discovered graph must pass validation before
new data or the manifest is published. On a normal filesystem error, published
files are rolled back to the previous state. Note: No multi-file rename system
is guaranteed to be fully crash-atomic if the computer powers off mid-publish.

The manifest is installed last. No graph files are automatically removed if a
source graph disappears or is renamed; such old data is simply omitted from the
new manifest. This avoids accidental data deletion. If none are discovered, the
script refuses to publish an empty manifest.

The lock file `.logseq-backup.lock` is local-only, Gitignored and held through
Git commit/push. It prevents accidental overlapping `logpush` runs.

If Logseq holds an exclusive lock on one of the source databases, quit the app
and retry. Native backup fallback may be used only if it passes existing age
and validation checks. A failure stops the entire multi-graph update.

## 5. First run checklist

```bash
cd ~/Code/Git/logseq-backup
python3 -m unittest discover -s tests -v
# Close Logseq first.
bash scripts/backup.sh
python3 -m json.tool data/mobile/manifest.json | head -60
sqlite3 data/Activity/db.sqlite 'PRAGMA integrity_check;'
sqlite3 data/Demo/db.sqlite 'PRAGMA integrity_check;'
git status
```

Review `data/mobile/manifest.json`, `data/mobile/demo.json`, and your existing
`data/mobile/activity.json` before allowing a push. Check that the private
GitHub repository is still private and that local `config.json` is ignored.

To send any pending commits and code updates, commit the patch's source files
manually, then `git push origin master` (or use `logpush` when `git.push=true`).

## 6. Compatibility

Local configs using the original `source_graph_dir` + `graph_name` remain in
single-graph mode. This permits rollback without altering current stored data.

The published `@logseq/cli` Build EDN exporter is used in the same way as the
previous Activity-only version. No Android code, credentials, network servers,
or third-party Python dependencies were added.
