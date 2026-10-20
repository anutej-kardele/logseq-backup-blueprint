# Running Logseq backups on macOS

The supported base workflow is manual: **quit Logseq (⌘Q), then run `bash scripts/backup.sh` in your private Git data repository**. The script discovers valid DB graphs, creates validated SQLite snapshots, exports JSON through the Logseq CLI, creates a manifest, and commits/pushes only when configured.

See the [README](../README.md) for first-time installation, dependency checks, configuration, and the optional `logpush` zsh function.

## Why close Logseq?

Some Logseq DB builds hold an exclusive SQLite lock while open. The script waits only for its configured snapshot timeout and may use a recent Logseq-native backup, which can omit newer notes. **For freshest data, close Logseq first.** The script itself never force-quits Logseq.

## Optional macOS Shortcuts workflow

Only configure automation **after `bash scripts/backup.sh` succeeds manually**, including Git push authentication from your normal account. Shortcuts can run in a more limited shell environment than Terminal, especially if Node was installed using NVM.

1. In the Shortcuts app, create a shortcut named **Logseq Daily Backup** with a **Run Shell Script** action; choose **zsh** and leave **Run as Administrator** off.
2. Paste this example, **replacing the private repo path and `node_bin` with the actual paths on your Mac** (`command -v node` and `command -v logseq` in Terminal). Both usually share the same directory.
3. Run it manually and verify the output log and the GitHub commit before setting a schedule.

```zsh
#!/bin/zsh

# Change these to the actual local paths for this Mac.
repo="$HOME/Code/Git/YOUR-PRIVATE-REPO"
node_bin="$HOME/.nvm/versions/node/YOUR_NODE_VERSION/bin"
export PATH="$node_bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

log="$HOME/Library/Logs/logseq-daily-backup.log"

if ! command -v node >/dev/null || ! command -v logseq >/dev/null; then
  echo '[error] Node or Logseq CLI missing from PATH'
  exit 1
fi
if [[ ! -f "$repo/scripts/backup.sh" ]]; then
  echo '[error] Backup script not found in configured private repository'
  exit 1
fi
if ! /usr/bin/curl -fsS --connect-timeout 5 --max-time 12 -o /dev/null https://api.github.com/; then
  echo '[error] GitHub is unreachable; no changes made'
  exit 1
fi

was_running=false
if /usr/bin/pgrep -x Logseq >/dev/null; then
  was_running=true
fi
reopen() {
  if [[ "$was_running" == true ]]; then
    /usr/bin/open -a Logseq
  fi
}
trap reopen EXIT

if [[ "$was_running" == true ]]; then
  /usr/bin/osascript -e 'tell application "Logseq" to quit' || exit 1
  for i in {1..40}; do
    /usr/bin/pgrep -x Logseq >/dev/null || break
    /bin/sleep 1
  done
  if /usr/bin/pgrep -x Logseq >/dev/null; then
    echo '[error] Logseq did not quit; backup skipped'
    exit 1
  fi
fi

if (cd "$repo" && /bin/bash scripts/backup.sh) >"$log" 2>&1; then
  echo "[ok] Backup completed; see $log"
else
  echo "[error] Backup failed; see $log"
  /bin/cat "$log"
  exit 1
fi
```

**Note:** This script has **no time-of-day or once-per-day limit**. A manual Play action runs whenever you click it. If desired, use the macOS Shortcuts **Automation** sidebar to schedule a Time of Day trigger. Available unattended execution options vary by macOS version and permissions. In shortcut details, check **Privacy → Allow Running When Locked**, if offered, and authorize shell access. If the automation only notifies you to press **Run**, it is *not* yet unattended.

**Sleep/offline caution:** A timed automation may not fire while the Mac sleeps and may skip the backup if GitHub cannot be reached. For retry-on-wake behavior, consider a properly tested per-user `launchd` agent; do not assume Shortcuts itself retries automatically. A connectivity check does not guarantee Git credentials are usable.

**Edit-safety caution:** This example requests that Logseq quit gracefully and reopens it afterward **only if it was running**. It can interrupt your work; do not enable unattended quitting until you have verified note persistence and app permissions. The core backup script's own lock prevents overlapping backup operations.
