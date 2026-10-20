# Logseq Backup

Back up **Logseq DB graphs** on macOS to a **private Git repository**. One command discovers all graphs, snapshots their SQLite databases, exports mobile-readable JSON, generates `data/mobile/manifest.json`, and optionally commits/pushes the result.

**Keep the repository containing your notes PRIVATE.** This public template contains code only; Git history retains old versions of notes even if you later delete them.

## Requirements

- macOS, Git, Python 3.10+, and a Logseq **DB-version** graph (each graph has `db.sqlite`).
- Logseq EDN CLI for JSON export: check `logseq --help`, or install Node.js and run `npm install -g @logseq/cli`.
- An existing **private GitHub repository**, cloned to your Mac, with Git authentication set up.

## Set up in your own private repository

1. Create and **clone an empty PRIVATE GitHub repository**. Download this public template as a ZIP, and extract **its contents into the root of your private clone** (the folder containing `.git`). Do not use this public template's Git remote for backups.
2. From that private repository's folder, run:

   ```bash
   cp config.example.json config.json
   # Edit config.json for your own Mac
   git remote -v
   git branch --show-current
   ```

3. In `config.json`, set `source_graphs_dir` (usually `~/logseq/graphs`), set `git.branch` to your actual branch (`main` or `master`), and leave `git.push` as `false` for the first run. The remote is read from Git (`origin`), **not** a URL or token in config.
4. Quit Logseq completely (**⌘Q**), then run:

   ```bash
   python3 -m unittest discover -s tests -v
   bash scripts/backup.sh
   python3 -m json.tool data/mobile/manifest.json
   ```

5. Verify your notes and JSON are correct. Change `git.push` to `true`, then run `bash scripts/backup.sh` again. The script pushes to the configured private remote and branch. Git credentials come from your existing Git setup.

## Output (generated automatically)

```text
data/
├── <GraphName>/db.sqlite       # one per discovered graph
└── mobile/
    ├── <graph-id>.json         # one per discovered graph
    └── manifest.json           # graph list for the Android reader
```

Graphs are detected from subdirectories of `source_graphs_dir` containing `db.sqlite`. Existing JSON schemas and paths are unchanged. **Close Logseq before backup**: its SQLite database may be exclusively locked while the app runs. The script refuses stale or invalid snapshots rather than replacing a good backup.

## Optional `logpush` terminal shortcut (zsh)

Add this to `~/.zshrc`, replacing the path with **your private repository folder**:

```bash
logpush() {
  local logfile="${TMPDIR:-/tmp/}logseq-backup.log"
  if (cd "$HOME/Code/Git/YOUR-PRIVATE-REPO" && bash scripts/backup.sh) >"$logfile" 2>&1; then
    return 0   # quiet on success
  else
    cat "$logfile"  # show errors
    return 1
  fi
}
```

Run `source ~/.zshrc`, then `logpush` from any directory. Inspect the most recent run with `cat "${TMPDIR:-/tmp/}logseq-backup.log"`.

## Security and restoration

`config.json` is Gitignored. Never put tokens in the code or push notes to a public repository. The backed-up `db.sqlite` is the restore source; JSON is intended for read-only mobile viewing. To test recovery, restore a copied SQLite backup into a **new** Logseq graph, not your active graph.

See `docs/MULTIGRAPH.md` for the manifest format and validation behavior, and `docs/LIVE_LOGSEQ_BACKUPS.md` for SQLite locking and fallback rules.
