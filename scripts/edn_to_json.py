#!/usr/bin/env python3
"""Convert Logseq DB EDN to read-only JSON.

Accepts the desktop :datoms export AND the published @logseq/cli
:pages-and-blocks + :classes Build EDN export. The output is a
viewer representation, never a replacement for a SQLite restore backup.
No third-party Python dependencies.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
from uuid import UUID, uuid5, NAMESPACE_URL
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

REPO = Path(__file__).resolve().parents[1]


class EDNParseError(ValueError):
    pass


class EDNReader:
    """Conservative EDN reader for Logseq's datom export, not a general Clojure runtime."""

    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.length = len(text)

    def space(self) -> None:
        while self.pos < self.length:
            c = self.text[self.pos]
            if c.isspace() or c == ',':
                self.pos += 1
            elif c == ';':
                end = self.text.find('\n', self.pos)
                self.pos = self.length if end < 0 else end + 1
            else:
                break

    def atom(self) -> str:
        start = self.pos
        while self.pos < self.length and self.text[self.pos] not in ' \r\n\t,{}[]()";':
            self.pos += 1
        if start == self.pos:
            raise EDNParseError(f"Unexpected token at offset {self.pos}")
        return self.text[start:self.pos]

    def string(self) -> str:
        start = self.pos
        self.pos += 1
        while self.pos < self.length:
            ch = self.text[self.pos]
            self.pos += 1
            if ch == '\\':
                self.pos += 1
            elif ch == '"':
                try:
                    return json.loads(self.text[start:self.pos])
                except json.JSONDecodeError as exc:
                    raise EDNParseError(f"Invalid escaped string at {start}: {exc}") from exc
        raise EDNParseError(f"Unterminated string at offset {start}")

    def read(self) -> Any:
        self.space()
        if self.pos >= self.length:
            raise EDNParseError('Unexpected end of file')
        ch = self.text[self.pos]
        if ch == '"':
            return self.string()
        if ch in '{[(':
            opener = ch
            closer = {'{': '}', '[': ']', '(': ')'}[ch]
            self.pos += 1
            items = []
            while True:
                self.space()
                if self.pos >= self.length:
                    raise EDNParseError(f'Missing {closer} at offset {self.pos}')
                if self.text[self.pos] == closer:
                    self.pos += 1
                    break
                items.append(self.read())
            if opener == '{':
                if len(items) % 2:
                    raise EDNParseError('EDN map must contain key/value pairs')
                result = {}
                for i in range(0, len(items), 2):
                    key = items[i]
                    if not isinstance(key, (str, int, float)):
                        raise EDNParseError(f'Unsupported EDN map key: {key!r}')
                    result[str(key)] = items[i+1]
                return result
            return items
        if ch == '#':
            self.pos += 1
            if self.pos < self.length and self.text[self.pos] == '{':
                values = self.read()
                return values
            tag = self.atom()
            if tag not in ('uuid', 'inst'):
                raise EDNParseError(f'Unsupported EDN tagged literal: #{tag}')
            value = self.read()
            if not isinstance(value, str):
                raise EDNParseError(f'Invalid #{tag} value: {value!r}')
            # Preserve UUIDs and date instants as plain JSON strings.
            return value
        if ch == '^':
            self.pos += 1
            self.read()  # metadata ignored
            return self.read()
        atom = self.atom()
        if atom == 'nil':
            return None
        if atom == 'true':
            return True
        if atom == 'false':
            return False
        if re.fullmatch(r'[+-]?\d+', atom):
            return int(atom)
        if re.fullmatch(r'[+-]?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?', atom):
            return float(atom)
        if re.fullmatch(r'[+-]?\d+(?:[eE][+-]?\d+)', atom):
            return float(atom)
        return atom  # keywords, symbols, ratios are kept as strings

    def parse(self) -> Any:
        value = self.read()
        self.space()
        if self.pos != self.length:
            raise EDNParseError(f'Unexpected content after EDN at offset {self.pos}')
        return value


def parse_edn_file(path: Path) -> dict:
    obj = EDNReader(path.read_text(encoding='utf-8')).parse()
    if not isinstance(obj, dict):
        raise ValueError('Expected a Logseq DB EDN map')
    if obj.get(':logseq.db.sqlite.export/graph-format') == ':datoms':
        datoms = obj.get(':datoms')
        if not isinstance(datoms, list) or not datoms:
            raise ValueError('Missing or empty :datoms vector')
        return obj
    if ':pages-and-blocks' in obj:
        records = obj[':pages-and-blocks']
        classes = obj.get(':classes')
        if (not isinstance(records, list) or not isinstance(classes, dict) or
                obj.get(':logseq.db.sqlite.export/export-type') != ':graph'):
            raise ValueError('Expected Logseq CLI :pages-and-blocks graph export')
        if not records and not classes:
            raise ValueError('Logseq CLI EDN graph has no pages or classes')
        return obj
    raise ValueError('Unrecognized Logseq DB EDN export format; expected :datoms or :pages-and-blocks')


def scalar_or_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def entity_refs(value: Any, entities: dict) -> list[dict]:
    out = []
    for key in scalar_or_list(value):
        if not isinstance(key, int):
            continue
        ref = entities.get(key, {})
        out.append({
            'entityId': key,
            'uuid': ref.get(':block/uuid'),
            'name': ref.get(':block/title') or ref.get(':block/name'),
        })
    return out


def compile_graph(obj: dict, graph_name: str) -> dict:
    datoms = obj[':datoms']
    entities = {}
    for row in datoms:
        if (not isinstance(row, list) or len(row) != 3 or
                not isinstance(row[0], int) or not isinstance(row[1], str)):
            raise ValueError(f'Unexpected Logseq datom: {str(row)[:120]}')
        ident, attr, value = row
        entry = entities.setdefault(ident, {})
        if attr in entry:
            if attr not in (':block/tags', ':block/refs', ':logseq.property.class/extends',
                            ':logseq.property.class/properties'):
                raise ValueError(f'Unexpected repeated datom attribute {attr} on entity {ident}')
            if not isinstance(entry[attr], list):
                entry[attr] = [entry[attr]]
            entry[attr].append(value)
        else:
            entry[attr] = value

    # Graph pages include journal pages and user-created classes/tags.
    # Built-in Logseq schema entities are deliberately excluded.
    pages = {
        ident: ent for ident, ent in entities.items()
        if ':block/name' in ent and ':block/uuid' in ent and
        not ent.get(':logseq.property/built-in?') and
        (':block/journal-day' in ent or
         (isinstance(ent.get(':db/ident'), str) and ent[':db/ident'].startswith(':user.class/')) or
         ':db/ident' not in ent)
    }
    # Blocks must have both a page and a parent, and must not be built-in schema entities.
    # Internal Logseq system views (e.g. $$$views) also have block/page.
    # Do not treat them as user notes or report them as lost user blocks.
    internal_block_count = sum(
        1 for ent in entities.values()
        if ':block/page' in ent and ':block/parent' in ent and
        ent.get(':block/page') not in pages and
        ent.get(':block/page') in entities and
        entities[ent[':block/page']].get(':logseq.property/built-in?')
    )
    blocks = {
        ident: ent for ident, ent in entities.items()
        if ':block/page' in ent and ':block/parent' in ent and
        not ent.get(':logseq.property/built-in?') and
        ent[':block/page'] in pages
    }

    children_by_parent = defaultdict(list)
    bad_parent = []
    for ident, ent in blocks.items():
        parent = ent[':block/parent']
        page = ent[':block/page']
        if not isinstance(parent, int) or not isinstance(page, int):
            raise ValueError(f'Block {ident} has unexpected page or parent format')
        if page not in pages:
            bad_parent.append(ident)
            continue
        if parent not in blocks and parent not in pages:
            bad_parent.append(ident)
            continue
        children_by_parent[parent].append(ident)
    if bad_parent:
        raise ValueError(f'{len(bad_parent)} blocks have missing parent/page references: {bad_parent[:10]}')

    def sort_key(entity_id: int):
        order = blocks[entity_id].get(':block/order')
        return (order if isinstance(order, str) else '\uffff', entity_id)

    def extra_attributes(ent: dict) -> dict:
        return {key.removeprefix(':'): val for key, val in ent.items()
                if not key.startswith(':block/') and not key.startswith(':db/')
                and not key.startswith(':logseq.property/built-in?')}

    def block_tree(ident: int, page_id: int, visited: set[int]) -> dict:
        if ident in visited:
            raise ValueError(f'Cycle or duplicated block reference at entity {ident}')
        visited.add(ident)
        ent = blocks[ident]
        if ent[':block/page'] != page_id:
            raise ValueError(f'Block {ident} references an inconsistent parent/page')
        return {
            'entityId': ident,
            'uuid': ent.get(':block/uuid'),
            'content': ent.get(':block/title', ''),
            'order': ent.get(':block/order'),
            'collapsed': bool(ent.get(':block/collapsed?', False)),
            'createdAt': ent.get(':block/created-at'),
            'updatedAt': ent.get(':block/updated-at'),
            'tags': entity_refs(ent.get(':block/tags'), entities),
            'references': entity_refs(ent.get(':block/refs'), entities),
            'properties': extra_attributes(ent),
            'children': [block_tree(child, page_id, visited)
                         for child in sorted(children_by_parent.get(ident, []), key=sort_key)],
        }

    visited = set()
    page_list = []
    for ident, ent in sorted(pages.items(), key=lambda x: (str(x[1].get(':block/title', '')).casefold(), x[0])):
        page_list.append({
            'entityId': ident,
            'uuid': ent.get(':block/uuid'),
            'title': ent.get(':block/title', ent.get(':block/name', '')),
            'name': ent[':block/name'],
            'kind': 'journal' if ':block/journal-day' in ent else
                    ('tag' if str(ent.get(':db/ident', '')).startswith(':user.class/') else 'page'),
            'journalDay': ent.get(':block/journal-day'),
            'createdAt': ent.get(':block/created-at'),
            'updatedAt': ent.get(':block/updated-at'),
            'tags': entity_refs(ent.get(':block/tags'), entities),
            'references': entity_refs(ent.get(':block/refs'), entities),
            'properties': extra_attributes(ent),
            'blocks': [block_tree(child, ident, visited)
                       for child in sorted(children_by_parent.get(ident, []), key=sort_key)],
        })
    if len(visited) != len(blocks):
        orphan = sorted(set(blocks) - visited)
        raise ValueError(f'{len(orphan)} blocks were not reached from their page: {orphan[:10]}')
    if not page_list:
        raise ValueError('No journal, page or user class entries found; check EDN format')

    schema = obj.get(':logseq.db.sqlite.export/schema-version', {})
    return {
        'schemaVersion': 1,
        'source': 'logseq-desktop-edn-datoms',
        'logseqSchema': schema,
        'graph': graph_name,
        'pageCount': len(page_list),
        'blockCount': len(blocks),
        'excludedSystemViewBlocks': internal_block_count,
        'pages': page_list,
    }



def compile_cli_graph(obj: dict, graph_name: str) -> dict:
    """Convert published @logseq/cli Build EDN while preserving block order.

    Older CLI exports do not carry numeric DataScript entity IDs, and usually
    omit block UUIDs. Synthetic IDs are derived from page keys and tree paths;
    they are read-only identifiers, NOT the original Logseq database IDs.
    """
    records = obj[':pages-and-blocks']
    classes = obj[':classes']
    namespace = f'logseq-cli:{graph_name}:'

    def identity(key: str, supplied: object = None) -> tuple[int, str]:
        if isinstance(supplied, str):
            try:
                uuid = str(UUID(supplied))
            except ValueError as exc:
                raise ValueError(f'Invalid exported Logseq UUID for {key}') from exc
        else:
            uuid = str(uuid5(NAMESPACE_URL, namespace + key))
        # Use a negative synthetic integer to avoid implying these were
        # DataScript IDs. Deterministic, within JS's safe integer range.
        number = -(uuid5(NAMESPACE_URL, namespace + key).int % (2**52) + 1)
        return number, uuid

    def properties(val: object) -> dict:
        if val is None:
            return {}
        if not isinstance(val, dict):
            raise ValueError('Expected :build/properties to be an EDN map')
        return {k.lstrip(':'): v for k, v in val.items()}

    tags_by_keyword = {}
    tag_pages = []
    for keyword, klass in sorted(classes.items()):
        if not isinstance(keyword, str) or not keyword.startswith(':user.class/'):
            raise ValueError(f'Unexpected class key: {keyword!r}')
        if not isinstance(klass, dict) or not isinstance(klass.get(':block/title'), str):
            raise ValueError(f'Missing class title for {keyword}')
        title = klass[':block/title']
        entity_id, uuid = identity('class:' + keyword, klass.get(':block/uuid'))
        tag_ref = {'entityId': entity_id, 'uuid': uuid, 'name': title}
        tags_by_keyword[keyword] = tag_ref
        tag_pages.append({
            'entityId': entity_id, 'uuid': uuid, 'title': title,
            'name': title.casefold(), 'kind': 'tag', 'journalDay': None,
            'createdAt': None, 'updatedAt': None,
            'tags': [], 'references': [], 'properties': properties(klass.get(':build/properties')),
            'blocks': [],
        })

    # Logseq built-in Card class does not appear in :classes. Preserve it in
    # tags even though its page is internal and should not appear in pages.
    builtin_tags = {':logseq.class/Card': 'Card'}
    def tag_refs(raw: object) -> list[dict]:
        result = []
        for keyword in scalar_or_list(raw):
            if not isinstance(keyword, str):
                raise ValueError(f'Unexpected class reference: {keyword!r}')
            if keyword in tags_by_keyword:
                result.append(tags_by_keyword[keyword].copy())
            elif keyword in builtin_tags:
                ref_id, ref_uuid = identity('builtin-class:' + keyword)
                result.append({'entityId': ref_id, 'uuid': ref_uuid,
                               'name': builtin_tags[keyword]})
            else:
                # Preserve future Logseq built-in/user classes without lying
                # about their identity or silently discarding a tag.
                ref_id, ref_uuid = identity('unresolved-class:' + keyword)
                result.append({'entityId': ref_id, 'uuid': ref_uuid,
                               'name': keyword.split('/')[-1]})
        return result

    pages = tag_pages[:]
    excluded_views = 0
    user_blocks = 0
    def count_blocks(nodes: object) -> int:
        if not isinstance(nodes, list):
            raise ValueError('Expected :blocks/:build/children list')
        count = 0
        for b in nodes:
            if not isinstance(b, dict):
                raise ValueError('Expected :block map')
            count += 1 + count_blocks(b.get(':build/children', []))
        return count

    def build_tree(b: dict, page_key: str, path: tuple[int, ...]) -> dict:
        nonlocal user_blocks
        if not isinstance(b, dict) or not isinstance(b.get(':block/title'), str):
            raise ValueError('Logseq CLI block must contain :block/title string')
        children = b.get(':build/children', [])
        if not isinstance(children, list):
            raise ValueError('Invalid :build/children list')
        key = f'block:{page_key}:' + '/'.join(map(str, path))
        ident, uuid = identity(key, b.get(':block/uuid'))
        user_blocks += 1
        return {
            'entityId': ident,
            'uuid': uuid,
            'content': b[':block/title'],
            'order': '/'.join(f'{i:08d}' for i in path),
            'collapsed': bool(b.get(':block/collapsed?', False)),
            'createdAt': b.get(':block/created-at'),
            'updatedAt': b.get(':block/updated-at'),
            'tags': tag_refs(b.get(':build/tags')),
            'references': [],  # Inline links remain in content, not structured by CLI
            'properties': properties(b.get(':build/properties')),
            'children': [build_tree(child, page_key, (*path, i))
                         for i, child in enumerate(children)],
        }

    seen_pages = set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get(':page'), dict):
            raise ValueError('Expected :pages-and-blocks entries with :page map')
        page = record[':page']
        nodes = record.get(':blocks')
        if not isinstance(nodes, list):
            raise ValueError('Expected :blocks array on CLI page')
        prop_map = page.get(':build/properties', {})
        if prop_map is not None and not isinstance(prop_map, dict):
            raise ValueError('Invalid page properties')
        is_builtin = bool((prop_map or {}).get(':logseq.property/built-in?', False))
        if is_builtin:
            excluded_views += count_blocks(nodes)
            continue
        if ':build/journal' in page:
            day = page[':build/journal']
            if not isinstance(day, int):
                raise ValueError('Journal date must be YYYYMMDD integer')
            try:
                date = datetime.strptime(str(day), '%Y%m%d').date().isoformat()
            except ValueError as exc:
                raise ValueError(f'Invalid Logseq journal day: {day}') from exc
            title, page_key, kind = date, 'journal:' + str(day), 'journal'
        else:
            title = page.get(':block/title')
            if isinstance(title, str):
                page_key = 'page:' + title.casefold()
            elif ':block/title' not in page and isinstance(page.get(':block/uuid'), str):
                # Some legacy CLI exports contain UUID-only page records with
                # real blocks. Preserve their content rather than dropping them
                # or aborting the entire backup. Use the full UUID for identity,
                # and a short, visibly synthetic title for read-only viewers.
                try:
                    page_uuid = str(UUID(page[':block/uuid']))
                except ValueError as exc:
                    raise ValueError('Untitled CLI page has an invalid UUID') from exc
                title = f'Untitled page ({page_uuid[:8]})'
                page_key = 'page-uuid:' + page_uuid
            else:
                raise ValueError('Non-journal page is missing title and valid UUID')
            kind = 'page'
        if page_key in seen_pages:
            raise ValueError(f'Duplicate page in CLI EDN: {page_key}')
        seen_pages.add(page_key)
        ident, uuid = identity(page_key, page.get(':block/uuid'))
        pages.append({
            'entityId': ident, 'uuid': uuid,
            'title': title, 'name': title.casefold(),
            'kind': kind, 'journalDay': page.get(':build/journal'),
            'createdAt': page.get(':block/created-at'),
            'updatedAt': page.get(':block/updated-at'),
            'tags': tag_refs(page.get(':build/tags')), 'references': [],
            'properties': properties(prop_map),
            'blocks': [build_tree(b, page_key, (i,)) for i, b in enumerate(nodes)],
        })
    if not pages:
        raise ValueError('CLI EDN export contains no usable pages')
    pages.sort(key=lambda p: (p['title'].casefold(), p['entityId']))
    return {
        'schemaVersion': 1,
        'source': 'logseq-cli-edn-build',
        'logseqSchema': obj.get(':logseq.db.sqlite.export/schema-version', {}),
        'graph': graph_name,
        'pageCount': len(pages),
        'blockCount': user_blocks,
        'excludedSystemViewBlocks': excluded_views,
        'pages': pages,
    }


def convert_file(source: Path, destination: Path, graph_name: str) -> dict:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if destination.suffix.lower() != '.json':
        raise ValueError('Output file must have a .json extension')
    if not source.is_file():
        raise FileNotFoundError(f'EDN file not found: {source}')
    if source == destination:
        raise ValueError('Source EDN and destination JSON must be different')
    obj = parse_edn_file(source)
    result = (compile_graph(obj, graph_name) if ':datoms' in obj
              else compile_cli_graph(obj, graph_name))
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.parent,
                                         prefix='.edn-json-', suffix='.tmp', delete=False) as out:
            tmp = Path(out.name)
            json.dump(result, out, indent=2, ensure_ascii=False, sort_keys=True)
            out.write('\n')
        # Confirm output is valid JSON before replacing an existing good export.
        with tmp.open(encoding='utf-8') as f:
            checked = json.load(f)
        if checked['pageCount'] != len(checked['pages']):
            raise ValueError('JSON page count inconsistent')
        os.replace(tmp, destination)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, help='Manually exported EDN file (overrides config)')
    parser.add_argument('--config', type=Path, default=REPO / 'config.json',
                        help='Ignored local config.json, used when --input is omitted')
    parser.add_argument('--output', type=Path, default=REPO / 'data/mobile/activity.json')
    parser.add_argument('--graph', help='Graph label in JSON (defaults to config.graph_name)')
    args = parser.parse_args()
    try:
        cfg = {}
        if args.config.is_file():
            cfg = json.loads(args.config.read_text(encoding='utf-8'))
        input_value = args.input or cfg.get('mobile_export', {}).get('edn_file')
        if not input_value:
            raise ValueError('EDN file required: supply --input /path/to/export.edn or set '
                             'mobile_export.edn_file in ignored config.json')
        name = args.graph or cfg.get('graph_name', 'Activity')
        result = convert_file(Path(input_value), args.output, name)
    except (OSError, ValueError, RecursionError) as exc:
        parser.exit(1, f'[error] {exc}\n')
    print(f"[ok] Exported {result['pageCount']} pages and {result['blockCount']} blocks")
    print(f"[info] Excluded {result['excludedSystemViewBlocks']} Logseq-internal view blocks")
    print(f'[path] {args.output.expanduser().resolve()}')
    print('[note] This JSON is a viewer export, not a restorable SQLite backup.')
    print('[note] This command reads an existing EDN file; backup.sh can generate a fresh one using the CLI.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
