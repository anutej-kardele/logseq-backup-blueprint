"""Integration tests for private multi-graph Logseq backups using fake data only."""
from __future__ import annotations

import fcntl
from contextlib import closing
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_edn_export import EXAMPLE_CLI_EDN

PROJECT = Path(__file__).resolve().parents[1]


class MultiGraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.repo = root / 'project'
        self.repo.mkdir()
        shutil.copytree(PROJECT / 'scripts', self.repo / 'scripts')
        shutil.copyfile(PROJECT / '.gitignore', self.repo / '.gitignore')
        (self.repo / 'data' / 'mobile').mkdir(parents=True)
        self.graphs = root / 'source' / 'graphs'
        self.graphs.mkdir(parents=True)
        self.make_graph('Activity')
        self.make_graph('Demo')
        self.fixture = root / 'test.edn'
        self.fixture.write_text(EXAMPLE_CLI_EDN, encoding='utf-8')
        self.fake_cli = root / 'fake-logseq'
        self.fake_cli.write_text(f'#!{sys.executable} -S\n' + '''\
import os, shutil, sys
from pathlib import Path
args=sys.argv[1:]
if '--help' in args:
    print('export-edn   Export DB graph as EDN')
    sys.exit(0)
if 'export-edn' in args:
    src=Path(args[args.index('-g')+1])
    dst=Path(args[args.index('-f')+1])
    assert src.name=='db.sqlite' and '.logseq-stage-' in str(src)
    if os.environ.get('FAKE_CLI_FAIL_GRAPH','').casefold() == src.parent.name.casefold():
        dst.write_text('corrupt-edn')
    else:
        shutil.copyfile(os.environ['FAKE_CLI_EDN'], dst)
    sys.exit(0)
sys.exit(2)
''', encoding='utf-8')
        self.fake_cli.chmod(0o755)
        self.cfg = {
            'source_graphs_dir': str(self.graphs),
            'auto_discover_graphs': True,
            'exclude_graphs': [],
            'snapshot': {'include_assets': True, 'max_file_mib': 95, 'timeout_seconds': 3,
                         'fallback_to_native_backup': False},
            'mobile_export': {'enabled': True, 'mode': 'edn-cli', 'cli_command': [str(self.fake_cli)],
                              'cli_style': 'legacy', 'timeout_seconds': 10, 'required': True},
            'git': {'enabled': True, 'push': False, 'branch': 'master', 'remote': 'origin'},
        }
        self.save()
        subprocess.run(['git', 'init', '-b', 'master'], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(['git', 'config', 'user.email', 'test@example.test'], cwd=self.repo, check=True)
        subprocess.run(['git', 'config', 'user.name', 'Backup Test'], cwd=self.repo, check=True)

    def make_graph(self, name: str):
        graph = self.graphs / name
        graph.mkdir()
        with closing(sqlite3.connect(graph / 'db.sqlite')) as db:
            db.execute('CREATE TABLE sample (title TEXT)')
            db.execute('INSERT INTO sample VALUES (?)', (name,))
            db.commit()

    def save(self):
        (self.repo / 'config.json').write_text(json.dumps(self.cfg))

    def backup(self, expect_success: bool = True, fail_graph: str = ''):
        env = os.environ.copy()
        env['FAKE_CLI_EDN'] = str(self.fixture)
        env['FAKE_CLI_FAIL_GRAPH'] = fail_graph
        proc = subprocess.run(['bash', 'scripts/backup.sh'], cwd=self.repo, env=env,
                              capture_output=True, text=True, timeout=30)
        if expect_success:
            self.assertEqual(proc.returncode, 0, proc.stdout + '\n' + proc.stderr)
        else:
            self.assertNotEqual(proc.returncode, 0, proc.stdout + '\n' + proc.stderr)
        return proc

    def test_two_graphs_manifest_and_stable_paths(self):
        out = self.backup()
        self.assertIn('Found 2 graph(s): Activity, Demo', out.stdout)
        for name in ('Activity', 'Demo'):
            db = self.repo / 'data' / name / 'db.sqlite'
            self.assertTrue(db.exists())
            with sqlite3.connect(db) as con:
                self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(con.execute('SELECT title FROM sample').fetchone()[0], name)
            data = json.loads((self.repo / 'data' / 'mobile' / f'{name.lower()}.json').read_text())
            self.assertEqual(data['graph'], name)
        manifest = json.loads((self.repo / 'data/mobile/manifest.json').read_text())
        self.assertEqual(manifest['schemaVersion'], 1)
        self.assertEqual([x['id'] for x in manifest['graphs']], ['activity', 'demo'])
        for entry in manifest['graphs']:
            file = self.repo / 'data' / 'mobile' / entry['file']
            self.assertEqual(entry['sha256'], hashlib.sha256(file.read_bytes()).hexdigest())
            self.assertEqual(entry['path'], f'data/mobile/{entry["file"]}')
        self.assertNotIn('config.json', subprocess.run(['git','ls-files'], cwd=self.repo,
                                                       capture_output=True,text=True).stdout)
        self.assertEqual(subprocess.run(['git','rev-list','--count','HEAD'], cwd=self.repo,
                                        capture_output=True,text=True).stdout.strip(), '1')
        self.assertIn('nothing to commit', self.backup().stdout)

    def test_failure_on_second_graph_preserves_all_published_files(self):
        self.backup()
        before = {str(p.relative_to(self.repo)): p.read_bytes()
                  for p in (self.repo / 'data').rglob('*') if p.is_file() and not p.name.startswith('.')}
        with sqlite3.connect(self.graphs / 'Activity' / 'db.sqlite') as db:
            db.execute("INSERT INTO sample VALUES ('new record')")
        out = self.backup(expect_success=False, fail_graph='Demo')
        self.assertIn('Logseq CLI did not produce a nonempty EDN export', out.stderr)
        for rel, content in before.items():
            self.assertEqual((self.repo / rel).read_bytes(), content, rel)

    def test_add_new_graph_and_remove_from_discovery_without_deleting_backups(self):
        self.backup()
        self.make_graph('University')
        self.backup()
        manifest = json.loads((self.repo / 'data/mobile/manifest.json').read_text())
        self.assertEqual([x['id'] for x in manifest['graphs']], ['activity','demo','university'])
        shutil.rmtree(self.graphs / 'Demo')
        self.backup()
        manifest = json.loads((self.repo / 'data/mobile/manifest.json').read_text())
        self.assertEqual([x['id'] for x in manifest['graphs']], ['activity','university'])
        self.assertTrue((self.repo / 'data/Demo/db.sqlite').exists())
        self.assertTrue((self.repo / 'data/mobile/demo.json').exists())

    def test_slug_collision_rejected_without_data_changes(self):
        self.backup()
        original = (self.repo / 'data/mobile/manifest.json').read_bytes()
        self.make_graph('Algo Pattern')
        self.make_graph('Algo-Pattern')
        proc = self.backup(expect_success=False)
        self.assertIn('same file ID', proc.stderr)
        self.assertEqual(original, (self.repo / 'data/mobile/manifest.json').read_bytes())

    def test_no_valid_graphs_must_not_replace_manifest(self):
        self.backup()
        old = (self.repo / 'data/mobile/manifest.json').read_bytes()
        shutil.rmtree(self.graphs / 'Demo')
        shutil.rmtree(self.graphs / 'Activity')
        self.backup(expect_success=False)
        self.assertEqual(old, (self.repo / 'data/mobile/manifest.json').read_bytes())

    def test_locked_wrapper_rejects_concurrent_backup(self):
        lockfile = self.repo / '.logseq-backup.lock'
        with lockfile.open('a+') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.backup(expect_success=False)
            self.assertIn('already running', result.stderr)
        self.assertFalse((self.repo / 'data/mobile/manifest.json').exists())

    def test_optional_json_is_not_allowed_in_multi_graph_mode(self):
        self.cfg['mobile_export']['required'] = False
        self.save()
        result = self.backup(expect_success=False)
        self.assertIn('required=true', result.stderr)

    def test_excludes_graph_and_ignores_symlink(self):
        (self.graphs / 'NonGraph').mkdir()
        (self.graphs / 'SymlinkDemo').symlink_to(self.graphs / 'Demo')
        self.cfg['exclude_graphs'] = ['demo']
        self.save()
        self.backup()
        doc = json.loads((self.repo / 'data/mobile/manifest.json').read_text())
        self.assertEqual([x['name'] for x in doc['graphs']], ['Activity'])

    def test_publish_rollback_restores_previous_versions(self):
        from unittest.mock import patch
        sys.path.insert(0, str(PROJECT / 'scripts'))
        import multi_graph
        root = Path(self.tmp.name) / 'atomic-test'
        root.mkdir()
        published_a, published_b = root / 'a', root / 'b'
        staged_a, staged_b = root / 'new-a', root / 'new-b'
        published_a.write_text('old-a')
        published_b.write_text('old-b')
        staged_a.write_text('new-a')
        staged_b.write_text('new-b')
        original = os.replace
        fail_once = [True]

        def injected_failure(source, destination):
            if Path(source) == staged_b and fail_once[0]:
                fail_once[0] = False
                raise OSError('synthetic publishing failure')
            return original(source, destination)

        with patch.object(multi_graph.os, 'replace', side_effect=injected_failure):
            with self.assertRaisesRegex(OSError, 'synthetic publishing failure'):
                multi_graph.publish_all(
                    [(staged_a, published_a), (staged_b, published_b)], root / 'rollback'
                )
        self.assertEqual(published_a.read_text(), 'old-a')
        self.assertEqual(published_b.read_text(), 'old-b')



if __name__ == '__main__':
    unittest.main()
