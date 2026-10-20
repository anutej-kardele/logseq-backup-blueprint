"""Tests exercise modern/legacy CLI paths against synthetic EDN, never user notes."""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_edn_export import EXAMPLE_EDN, EXAMPLE_CLI_EDN

ROOT = Path(__file__).resolve().parents[1]


class AutomatedExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        shutil.copytree(ROOT / 'scripts', self.project / 'scripts')
        shutil.copy2(ROOT / '.gitignore', self.project / '.gitignore')
        (self.project / 'data').mkdir()
        self.source = self.root / 'source' / 'Activity'
        self.source.mkdir(parents=True)
        self.con = sqlite3.connect(self.source / 'db.sqlite')
        self.con.execute('CREATE TABLE test_notes (value TEXT)')
        self.con.execute("INSERT INTO test_notes VALUES ('Hello')")
        self.con.commit()
        self.fixture = self.root / 'test.edn'
        self.fixture.write_text(EXAMPLE_EDN, encoding='utf-8')
        self.cli = self.root / 'fake-logseq'
        self.cli.write_text(f'#!{sys.executable}\n' + r'''\
import os, shutil, sys, time
from pathlib import Path
args=sys.argv[1:]
if '--help' in args:
    print('  graph    Graph commands' if os.environ.get('CLI_FLAVOR','modern')=='modern' else '  export-edn     Export DB graph as EDN')
    sys.exit(0)
if 'server' in args and 'stop' in args:
    sys.exit(0)
if 'export' in args or 'export-edn' in args:
    if 'export-edn' in args:
        source = Path(args[args.index('-g')+1]); target = Path(args[args.index('-f')+1])
        assert source.name == 'db.sqlite' and '.logseq-stage-' in str(source)
    else:
        root = Path(args[args.index('--root-dir')+1]); graph = args[args.index('--graph')+1]
        assert (root/'graphs'/graph/'db.sqlite').exists()
        target = Path(args[args.index('--file')+1])
    if os.environ.get('CLI_BAD_EXPORT') == '1':
        target.write_text('invalid-EDN')
    else:
        shutil.copyfile(os.environ['CLI_TEST_EDN'], target)
    sys.exit(0)
sys.exit(2)
'''.replace('\\\n','\n'), encoding='utf-8')
        self.cli.chmod(0o755)
        self.config = {
            'source_graph_dir': str(self.source), 'graph_name': 'Activity',
            'snapshot': {'include_assets': True, 'max_file_mib': 95, 'timeout_seconds': 3},
            'mobile_export': {'enabled': True, 'mode': 'edn-cli', 'cli_style': 'auto',
                              'cli_command': [str(self.cli)], 'timeout_seconds': 10, 'required': True},
            'git': {'enabled': True, 'push': False, 'branch': 'master', 'remote': 'origin'},
        }
        subprocess.run(['git','init','-b','master'],cwd=self.project,check=True,capture_output=True)
        subprocess.run(['git','config','user.email','test@example.org'],cwd=self.project,check=True)
        subprocess.run(['git','config','user.name','Backup Test'],cwd=self.project,check=True)
        self._save()

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def _save(self):
        (self.project/'config.json').write_text(json.dumps(self.config))

    def run_backup(self, *, flavor='modern', bad=False, ok=True, timeout=15):
        import os
        env=os.environ.copy()
        env['CLI_FLAVOR']=flavor
        env['CLI_BAD_EXPORT']='1' if bad else '0'
        env['CLI_TEST_EDN']=str(self.fixture)
        proc=subprocess.run(['bash','scripts/backup.sh'],cwd=self.project,
                            env=env,capture_output=True,text=True,timeout=timeout)
        if ok:
            self.assertEqual(proc.returncode, 0, proc.stdout+proc.stderr)
        else:
            self.assertNotEqual(proc.returncode, 0, proc.stdout+proc.stderr)
        return proc

    def test_modern_cli_snapshot_and_json_are_consistent(self):
        output=self.run_backup()
        self.assertIn('CLI export mode: modern', output.stdout)
        db=self.project/'data/Activity/db.sqlite'
        data=json.loads((self.project/'data/mobile/activity.json').read_text())
        self.assertEqual(data['pageCount'], 2)
        self.assertEqual(data['blockCount'], 2)
        con=sqlite3.connect(db)
        try:
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(con.execute('PRAGMA journal_mode').fetchone()[0],'delete')
        finally:
            con.close()
        self.assertFalse(list((self.project/'data').glob('.logseq-stage-*')))
        self.assertEqual(int(subprocess.run(['git','rev-list','--count','HEAD'],
                                            cwd=self.project,capture_output=True,text=True).stdout.strip()), 1)
        self.assertIn('nothing to commit', self.run_backup().stdout)

    def test_legacy_cli_export_from_staged_database(self):
        self.fixture.write_text(EXAMPLE_CLI_EDN, encoding='utf-8')
        output=self.run_backup(flavor='legacy')
        self.assertIn('CLI export mode: legacy', output.stdout)
        data=json.loads((self.project/'data/mobile/activity.json').read_text())
        self.assertEqual(data['pageCount'], 3)
        self.assertEqual(data['blockCount'], 4)
        self.assertEqual(data['source'], 'logseq-cli-edn-build')

    def test_invalid_edn_keeps_previous_db_and_json(self):
        self.run_backup()
        db=self.project/'data/Activity/db.sqlite'
        j=self.project/'data/mobile/activity.json'
        old_db=db.read_bytes()
        old_json=j.read_bytes()
        self.con.execute("INSERT INTO test_notes VALUES ('Change')")
        self.con.commit()
        output=self.run_backup(bad=True,ok=False)
        self.assertIn('existing DB and JSON were preserved', output.stderr)
        self.assertEqual(db.read_bytes(), old_db)
        self.assertEqual(j.read_bytes(), old_json)

    def test_exclusive_live_lock_falls_back_to_recent_logseq_backup(self):
        # Create a Logseq-style completed snapshot before taking an exclusive lock.
        native = self.source / 'backup' / '2026-10-19-20-00' / 'db.sqlite'
        native.parent.mkdir(parents=True)
        with sqlite3.connect(native) as dst:
            self.con.backup(dst)
        self.config['snapshot'].update({
            'timeout_seconds': 1, 'fallback_to_native_backup': True,
            'native_backup_max_age_minutes': 120,
        })
        self._save()
        self.con.execute('PRAGMA locking_mode=EXCLUSIVE')
        self.con.execute('BEGIN EXCLUSIVE')
        try:
            result = self.run_backup(flavor='legacy', timeout=12)
            self.assertIn('Used Logseq native snapshot', result.stdout)
            self.assertTrue((self.project/'data/Activity/db.sqlite').exists())
            self.assertTrue((self.project/'data/mobile/activity.json').exists())
        finally:
            self.con.rollback()
            self.con.execute('PRAGMA locking_mode=NORMAL')

    def test_native_fallback_never_replaces_newer_published_snapshot(self):
        self.run_backup()
        db = self.project / 'data/Activity/db.sqlite'
        old = db.read_bytes()
        native = self.source / 'backup' / 'old' / 'db.sqlite'
        native.parent.mkdir(parents=True)
        with sqlite3.connect(native) as dst:
            self.con.backup(dst)
        import os
        older = time.time() - 60
        os.utime(native, (older, older))
        self.con.execute('PRAGMA locking_mode=EXCLUSIVE')
        self.con.execute('BEGIN EXCLUSIVE')
        self.config['snapshot'].update({
            'timeout_seconds': 1, 'fallback_to_native_backup': True,
            'native_backup_max_age_minutes': 120,
        })
        self._save()
        try:
            result = self.run_backup(ok=False, timeout=12)
            self.assertIn('refusing to roll back data', result.stdout)
            self.assertEqual(db.read_bytes(), old)
        finally:
            self.con.rollback()
            self.con.execute('PRAGMA locking_mode=NORMAL')

    def test_snapshot_timeout_preserves_last_good_backup(self):
        self.run_backup()
        db=self.project/'data/Activity/db.sqlite'
        old=db.read_bytes()
        # Simulate a backed-up process stuck waiting on an SQLite writer.
        (self.project/'scripts/snapshot_worker.py').write_text('import time\ntime.sleep(15)\n')
        self.config['snapshot']['timeout_seconds']=1
        self._save()
        begin=time.monotonic()
        output=self.run_backup(ok=False,timeout=8)
        self.assertLess(time.monotonic()-begin,7)
        self.assertIn('Previous GitHub/local snapshot was preserved',output.stderr)
        self.assertEqual(db.read_bytes(),old)


if __name__=='__main__':
    unittest.main()
