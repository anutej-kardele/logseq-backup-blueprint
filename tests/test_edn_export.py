"""EDN converter unit tests using invented notes, not real user data."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import sys
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'scripts'))
from edn_to_json import EDNReader, EDNParseError, convert_file, compile_graph


EXAMPLE_EDN = r'''{:logseq.db.sqlite.export/schema-version {:major 65 :minor 34}
 :logseq.db.sqlite.export/graph-format :datoms
 :datoms [[1 :block/name "Oct 19th, 2026"]
          [1 :block/title "Oct 19th, 2026"]
          [1 :block/uuid #uuid "00000000-0000-0000-0000-000000000001"]
          [1 :block/journal-day 20261019]
          [2 :block/name "study"]
          [2 :block/title "Study"]
          [2 :block/uuid #uuid "00000000-0000-0000-0000-000000000002"]
          [2 :db/ident :user.class/study-A]
          [3 :block/page 1]
          [3 :block/parent 1]
          [3 :block/order "a1"]
          [3 :block/title "Root \\ line"]
          [3 :block/uuid #uuid "00000000-0000-0000-0000-000000000003"]
          [3 :block/tags 2]
          [3 :block/refs 2]
          [4 :block/page 1]
          [4 :block/parent 3]
          [4 :block/order "a1"]
          [4 :block/title "Nested"]
          [4 :block/uuid #uuid "00000000-0000-0000-0000-000000000004"]
          [5 :block/name "$$$views"]
          [5 :logseq.property/built-in? true]
          [6 :block/page 5]
          [6 :block/parent 5]
          [6 :block/title "All"]]
 :logseq.db.sqlite.export/export-type :graph}'''


# Synthetic fixture follows the actual @logseq/cli export shape observed in
# activity-cli.edn. No user's personal notes are checked into the project.
EXAMPLE_CLI_EDN = r'''{:classes
  {:user.class/study-abc
    {:block/title "Study" :block/uuid #uuid "10000000-0000-0000-0000-000000000001"}}
  :properties {}
  :pages-and-blocks
  [{:page {:block/title "$$$views"
           :build/properties {:logseq.property/built-in? true}}
    :blocks [{:block/title "All" :build/properties {:logseq.property.view/feature-type :all-pages}}]}
   {:page {:build/journal 20261019}
    :blocks [{:block/title "Root [[Study]]"
              :build/tags [:user.class/study-abc :logseq.class/Card]
              :build/properties {:logseq.property.fsrs/due 100}
              :build/children [{:block/title "Nested note" :block/collapsed? true}]}
             {:block/title "Second"}]}
   {:page {:block/title "Custom Page"} :blocks [{:block/title "Other page block"}]}]
  :logseq.db.sqlite.export/schema-version {:major 65 :minor 22}
  :logseq.db.sqlite.export/export-type :graph}'''


class EDNExportTests(unittest.TestCase):
    def test_datom_tree_tags_and_internal_views(self):
        with tempfile.TemporaryDirectory() as root:
            src = Path(root) / 'graph.edn'
            dst = Path(root) / 'result.json'
            src.write_text(EXAMPLE_EDN, encoding='utf-8')
            result = convert_file(src, dst, 'Activity')
            self.assertEqual(result['pageCount'], 2)
            self.assertEqual(result['blockCount'], 2)
            self.assertEqual(result['excludedSystemViewBlocks'], 1)
            j = next(p for p in result['pages'] if p['kind'] == 'journal')
            self.assertEqual(j['journalDay'], 20261019)
            self.assertEqual(j['blocks'][0]['tags'][0]['name'], 'Study')
            self.assertEqual(j['blocks'][0]['children'][0]['content'], 'Nested')
            self.assertEqual(json.loads(dst.read_text()), result)

    def test_converter_is_deterministic(self):
        with tempfile.TemporaryDirectory() as root:
            src = Path(root) / 'graph.edn'
            dst = Path(root) / 'result.json'
            src.write_text(EXAMPLE_EDN, encoding='utf-8')
            convert_file(src, dst, 'Activity')
            first = dst.read_bytes()
            convert_file(src, dst, 'Activity')
            self.assertEqual(first, dst.read_bytes())

    def test_invalid_export_does_not_replace_last_good_json(self):
        with tempfile.TemporaryDirectory() as root:
            src = Path(root) / 'bad.edn'
            dst = Path(root) / 'result.json'
            dst.write_text('{"last":"good"}')
            src.write_text('not-an-edn-graph')
            with self.assertRaises(ValueError):
                convert_file(src, dst, 'Activity')
            self.assertEqual(dst.read_text(), '{"last":"good"}')


    def test_cli_build_format_is_converted_to_same_mobile_schema(self):
        with tempfile.TemporaryDirectory() as root:
            src = Path(root) / 'cli.edn'
            dst = Path(root) / 'activity.json'
            src.write_text(EXAMPLE_CLI_EDN, encoding='utf-8')
            j = convert_file(src, dst, 'Activity')
            self.assertEqual(j['source'], 'logseq-cli-edn-build')
            self.assertEqual((j['pageCount'], j['blockCount'], j['excludedSystemViewBlocks']), (3, 4, 1))
            self.assertEqual(set(p['kind'] for p in j['pages']), {'journal', 'page', 'tag'})
            page = next(p for p in j['pages'] if p['kind'] == 'journal')
            self.assertEqual(page['journalDay'], 20261019)
            self.assertEqual(page['title'], '2026-10-19')
            rootblock = page['blocks'][0]
            self.assertEqual(rootblock['content'], 'Root [[Study]]')
            self.assertEqual([t['name'] for t in rootblock['tags']], ['Study', 'Card'])
            self.assertEqual(rootblock['properties']['logseq.property.fsrs/due'], 100)
            self.assertEqual(rootblock['children'][0]['content'], 'Nested note')
            self.assertTrue(rootblock['children'][0]['collapsed'])
            self.assertTrue(rootblock['uuid'])
            self.assertEqual(j, json.loads(dst.read_text()))
            previous = dst.read_bytes()
            convert_file(src, dst, 'Activity')
            self.assertEqual(previous, dst.read_bytes())

    def test_cli_uuid_only_page_preserves_real_blocks(self):
        # Regression: legacy @logseq/cli may export a page with only a UUID
        # and no :block/title or :build/journal, while still holding notes.
        edn = r'''{:classes {}
          :pages-and-blocks
          [{:page {:block/uuid #uuid "11111111-2222-3333-4444-555555555555"}
            :blocks [{:block/title "Retain this note"
                      :build/children [{:block/title "Nested content"}]}]}]
          :logseq.db.sqlite.export/export-type :graph}'''
        with tempfile.TemporaryDirectory() as root:
            src, dst = Path(root) / 'untitled.edn', Path(root) / 'export.json'
            src.write_text(edn, encoding='utf-8')
            exported = convert_file(src, dst, 'Activity')
            self.assertEqual(exported['pageCount'], 1)
            self.assertEqual(exported['blockCount'], 2)
            page = exported['pages'][0]
            self.assertEqual(page['title'], 'Untitled page (11111111)')
            self.assertEqual(page['kind'], 'page')
            self.assertEqual(page['uuid'], '11111111-2222-3333-4444-555555555555')
            self.assertEqual(page['blocks'][0]['content'], 'Retain this note')
            self.assertEqual(page['blocks'][0]['children'][0]['content'], 'Nested content')
            first = dst.read_bytes()
            convert_file(src, dst, 'Activity')
            self.assertEqual(dst.read_bytes(), first)

    def test_cli_missing_title_and_uuid_remains_an_error(self):
        edn = r'''{:classes {} :pages-and-blocks
          [{:page {} :blocks [{:block/title "Unknown page"}]}]
          :logseq.db.sqlite.export/export-type :graph}'''
        with tempfile.TemporaryDirectory() as root:
            src, dst = Path(root) / 'invalid.edn', Path(root) / 'export.json'
            src.write_text(edn, encoding='utf-8')
            dst.write_text('previous good export', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'missing title and valid UUID'):
                convert_file(src, dst, 'Activity')
            self.assertEqual(dst.read_text(encoding='utf-8'), 'previous good export')

    def test_cli_missing_children_are_rejected_without_overwriting_json(self):
        with tempfile.TemporaryDirectory() as root:
            src = Path(root) / 'bad.edn'
            dst = Path(root) / 'activity.json'
            dst.write_text('{"last":"good"}')
            src.write_text(EXAMPLE_CLI_EDN.replace(':build/children [{:block/title "Nested note" :block/collapsed? true}]',
                                                   ':build/children "not an array"'))
            with self.assertRaises(ValueError):
                convert_file(src, dst, 'Activity')
            self.assertEqual(dst.read_text(), '{"last":"good"}')

    def test_cli_unknown_structure_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            src=Path(root)/'bad.edn'
            dst=Path(root)/'x.json'
            src.write_text('{:pages-and-blocks [] :classes [] :logseq.db.sqlite.export/export-type :graph}')
            with self.assertRaises(ValueError):
                convert_file(src, dst, 'Activity')
            self.assertFalse(dst.exists())

    def test_unbalanced_edn_is_rejected(self):
        with self.assertRaises(EDNParseError):
            EDNReader('{:a [1 2 3}').parse()


if __name__ == '__main__':
    unittest.main()
