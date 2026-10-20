# Logseq Backup Blueprint

A macOS backup utility for **Logseq DB graphs**. One command discovers your graphs, snapshots their SQLite databases, exports mobile-readable JSON, generates `data/mobile/manifest.json`, and commits/pushes the backup to your **own private Git repository**.

**Privacy:** This public repository is **source code only**. Copy the template into a **separate PRIVATE GitHub repository** for your actual backups. Verify that the destination says **Private** on GitHub before enabling push. Deleting a committed file later does not remove it from Git history.

## Requirements (macOS)

- Logseq DB graphs (for example, `~/logseq/graphs/Activity/db.sqlite`)
- Git, Python 3.10+, Node.js/npm, and the Logseq CLI
- Your own **empty, private** GitHub repository with Git authentication configured

If needed, install developer tools using [Homebrew](https://brew.sh/):

```bash
brew install git python node
npm install -g @logseq/cli
```

If you already use **NVM** for Node.js, keep using it rather than installing another copy of Node.

Verify the tools:

```bash
git --version
python3 --version
node --version
npm --version
logseq --help
```

**Python packages:** No third-party pip packages are required; `requirements.txt` contains comments only. The optional command `python3 -m pip install -r requirements.txt` installs nothing. Install the **Logseq CLI with npm**, not pip.

## Setup: use the blueprint inside your private Git repository

**1.** Create an empty **private** repository on GitHub, then clone it onto your Mac:

```bash
cd ~/Code/Git
git clone https://github.com/USERNAME/YOUR-PRIVATE-REPO.git
cd YOUR-PRIVATE-REPO
```

**2.** Download the public blueprint and copy its contents into the root of your private clone (the folder with `.git`):

```bash
tmp="$(mktemp -d)"
curl -fsSL https://github.com/anutej-kardele/logseq-backup-blueprint/archive/refs/heads/main.zip -o "$tmp/blueprint.zip"
unzip -q "$tmp/blueprint.zip" -d "$tmp"
ditto "$tmp/logseq-backup-blueprint-main" .
rm -rf "$tmp"
```

Or download its ZIP through GitHub's **Code → Download ZIP** menu and extract the contents into the private clone.

**3.** Create local configuration and inspect the private remote:

```bash
cp config.example.json config.json
open -e config.json
git remote -v
git branch --show-current
```

In `config.json`, set `source_graphs_dir` (usually `~/logseq/graphs`) and set `git.branch` to your private repository's current branch (`main` or `master`). Leave `git.push` as `false` for the first test. Keep `auto_discover_graphs: true` to include all valid graphs. The Git URL and credentials are managed by **Git**; no URL or token is needed in this config.

**4.** Quit Logseq completely (**⌘Q**) to release the live SQLite database. Run tests and the first local backup:

```bash
python3 -m unittest discover -s tests -v
bash scripts/backup.sh
python3 -m json.tool data/mobile/manifest.json
```

Confirm `data/<GraphName>/db.sqlite`, `data/mobile/<graph-id>.json`, and `data/mobile/manifest.json` are correct. You can check SQLite integrity for one graph using `sqlite3 data/Activity/db.sqlite 'PRAGMA integrity_check;'` (replace `Activity` with your graph name); the expected result is `ok`.

**5.** Commit the blueprint source files to **your private repository** and enable pushes:

```bash
git status --short
git add .
git commit -m "chore: install Logseq backup blueprint"
git push -u origin main  # Replace main with your actual branch if different
```

`config.json` is Gitignored, and the generated backup data may already have been committed by `backup.sh`. **Before the push, verify on GitHub that this destination is PRIVATE; check `git remote -v` to avoid accidentally pushing notes to the public blueprint.**

Now set `git.push` to `true` in your ignored `config.json` and run:

```bash
bash scripts/backup.sh
```

The backup script commits changed backup data and pushes it to the configured private remote and branch. If there are no data changes, it may report that there is nothing to commit.

## Optional `logpush` command (zsh)

Add this function to `~/.zshrc`, replacing the path with **your private repository's folder**:

```bash
logpush() {
  local logfile="${TMPDIR:-/tmp/}logseq-backup.log"
  if (cd "$HOME/Code/Git/YOUR-PRIVATE-REPO" && bash scripts/backup.sh) > "$logfile" 2>&1; then
    return 0
  else
    cat "$logfile"
    return 1
  fi
}
```

Then reload and run from anywhere:

```bash
source ~/.zshrc
logpush
cat "${TMPDIR:-/tmp/}logseq-backup.log"
```

Success is quiet; failures print the backup log. **Quit Logseq before running `logpush`.**

## Output and restore

```text
data/
├── Activity/db.sqlite
├── Demo/db.sqlite
└── mobile/
    ├── activity.json
    ├── demo.json
    └── manifest.json
```

The graph names above are examples, discovered automatically. SQLite backups are for restoring data; the JSON files are for read-only mobile applications. To verify restoration, restore a **copy** of a SQLite backup as a separate Logseq graph, never over your active graph.

More details: [`docs/MULTIGRAPH.md`](docs/MULTIGRAPH.md) (manifest and graph discovery), [`docs/LIVE_LOGSEQ_BACKUPS.md`](docs/LIVE_LOGSEQ_BACKUPS.md) (locks and recovery), [`docs/EDN_EXPORT.md`](docs/EDN_EXPORT.md) (EDN/JSON compatibility), and [`docs/AUTOMATION.md`](docs/AUTOMATION.md) (optional macOS Shortcuts automation).

Licensed under the [MIT License](LICENSE).
