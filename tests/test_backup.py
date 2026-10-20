"""Integration tests; no Logseq installation or network required."""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


class BackupPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        shutil.copytree(PROJECT / "scripts", self.project / "scripts")
        shutil.copy2(PROJECT / ".gitignore", self.project / ".gitignore")
        (self.project / "data").mkdir()
        self.graph = self.root / "original" / "Activity"
        self.graph.mkdir(parents=True)
        self.conn = sqlite3.connect(str(self.graph / "db.sqlite"))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("CREATE TABLE sample_notes (title TEXT)")
        self.conn.execute("INSERT INTO sample_notes VALUES ('one')")
        self.conn.commit()
        (self.graph / "assets").mkdir()
        (self.graph / "assets" / "img.txt").write_text("attachment text")
        # A small fake CLI matching the documented JSON response envelope.
        self.fake_cli = self.root / "fake-logseq"
        self.fake_cli.write_text(f"#!{sys.executable}\n" + '''\
import json, sys
from pathlib import Path
args = sys.argv[1:]
root = Path(args[args.index('--root-dir')+1]); graph = args[args.index('--graph')+1]
assert (root/'graphs'/graph/'db.sqlite').exists(), 'missing isolated snapshot'
if 'list' in args and 'page' in args:
    print(json.dumps({'ok': True, 'data': {'items': [{'block/name':'Example'}]}}))
elif 'show' in args:
    print(json.dumps({'ok': True, 'data': {'root': {'block/title':'Example', 'block/children': [{'block/title':'Hello from test'}]}}}))
else:
    sys.exit(2)
''', encoding="utf-8")
        self.fake_cli.chmod(0o755)
        self.cfg = {
            "source_graph_dir": str(self.graph), "graph_name": "Activity",
            "snapshot": {"include_assets": True, "max_file_mib": 95},
            "mobile_export": {"enabled": True, "mode": "page-tree-cli", "cli_command": [str(self.fake_cli)], "required": False},
            "git": {"enabled": True, "push": False, "remote": "origin", "branch": "main"},
        }
        self.set_config()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.project, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.org"], cwd=self.project, check=True)
        subprocess.run(["git", "config", "user.name", "Backup Test"], cwd=self.project, check=True)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def set_config(self):
        (self.project / "config.json").write_text(json.dumps(self.cfg), encoding="utf-8")

    def backup(self, expected_success=True):
        out = subprocess.run(["bash", "scripts/backup.sh"], cwd=self.project,
                             capture_output=True, text=True, timeout=30)
        if expected_success:
            self.assertEqual(out.returncode, 0, out.stdout + "\n" + out.stderr)
        else:
            self.assertNotEqual(out.returncode, 0, out.stdout + "\n" + out.stderr)
        return out

    def test_consistent_live_wal_snapshot_and_json(self):
        self.backup()
        copy = self.project / "data" / "Activity" / "db.sqlite"
        with sqlite3.connect(copy) as backup:
            values = backup.execute("SELECT title FROM sample_notes").fetchall()
            self.assertEqual(values, [("one",)])
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual((self.project / "data" / "Activity" / "assets" / "img.txt").read_text(), "attachment text")
        export = json.loads((self.project / "data" / "mobile" / "activity.json").read_text())
        self.assertEqual(export["pageCount"], 1)
        self.assertEqual(export["pages"][0]["root"]["block/children"][0]["block/title"], "Hello from test")
        ignored = subprocess.run(["git", "check-ignore", "config.json"], cwd=self.project,
                                 capture_output=True, text=True)
        self.assertEqual(ignored.returncode, 0)
        tracked = subprocess.run(["git", "ls-files"], cwd=self.project, capture_output=True, text=True).stdout
        self.assertNotIn("config.json", tracked)
        self.assertIn("data/Activity/db.sqlite", tracked)

        # A second snapshot overwrites latest file, while Git retains history.
        self.conn.execute("INSERT INTO sample_notes VALUES ('two')")
        self.conn.commit()
        self.backup()
        with sqlite3.connect(copy) as backup:
            self.assertEqual(backup.execute("SELECT COUNT(*) FROM sample_notes").fetchone()[0], 2)
        self.assertEqual(len(list((self.project / "data" / "Activity").glob("db.sqlite"))), 1)
        commits = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=self.project,
                                 capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(commits, "2")

    def test_missing_optional_cli_does_not_block_sqlite(self):
        self.cfg["mobile_export"]["cli_command"] = [str(self.root / "missing-cli")]
        self.set_config()
        result = self.backup()
        self.assertIn("Mobile export not updated", result.stderr)
        self.assertTrue((self.project / "data" / "Activity" / "db.sqlite").exists())
        self.assertFalse((self.project / "data" / "mobile" / "activity.json").exists())
        status = json.loads((self.project / ".backup-status.json").read_text())
        self.assertIn("skipped", status["mobile_export"])

    def test_required_cli_export_fails_without_publishing(self):
        self.cfg["mobile_export"]["required"] = True
        self.cfg["mobile_export"]["cli_command"] = [str(self.root / "missing-cli")]
        self.set_config()
        self.backup(expected_success=False)
        self.assertFalse((self.project / "data" / "Activity" / "db.sqlite").exists())


if __name__ == "__main__":
    unittest.main()
