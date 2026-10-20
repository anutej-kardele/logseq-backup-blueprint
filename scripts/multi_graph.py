#!/usr/bin/env python3
"""Discover, stage, validate and publish multiple Logseq DB graphs.

All source graphs are read-only. Each run prepares every graph before touching
published data; publication rolls back on ordinary filesystem errors.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
from pathlib import Path


def graph_id(name: str) -> str:
    """Produce a simple URL/file-safe identifier; existing Activity -> activity."""
    ascii_name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    stem = re.sub(r'[^a-z0-9]+', '-', ascii_name.casefold()).strip('-')
    if not stem:
        stem = 'graph-' + hashlib.sha256(name.encode('utf-8')).hexdigest()[:12]
    if len(stem) > 70:
        stem = stem[:56].rstrip('-') + '-' + hashlib.sha256(name.encode('utf-8')).hexdigest()[:10]
    return stem


def discover_graphs(root: Path, excluded: list[str]) -> list[tuple[str, Path, str]]:
    """Immediate child folders with real db.sqlite. Never follow symlinks."""
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError(f'Logseq graphs folder missing or unsafe: {root}')
    if not isinstance(excluded, list) or not all(isinstance(x, str) for x in excluded):
        raise RuntimeError('exclude_graphs must be an array of graph folder names')
    excluded_set = {s.casefold() for s in excluded}
    results: list[tuple[str, Path, str]] = []
    used_ids: dict[str, str] = {}
    for folder in sorted(root.iterdir(), key=lambda x: (x.name.casefold(), x.name)):
        if folder.name.startswith('.') or not folder.is_dir() or folder.is_symlink():
            continue
        if folder.name.casefold() in excluded_set:
            continue
        database = folder / 'db.sqlite'
        if not database.is_file() or database.is_symlink():
            continue
        if folder.name.casefold() == 'mobile':
            raise RuntimeError("A graph named 'mobile' conflicts with the existing data/mobile directory")
        gid = graph_id(folder.name)
        if gid in used_ids:
            raise RuntimeError(
                f'Graph names {used_ids[gid]!r} and {folder.name!r} produce the same file ID {gid!r}. '
                'Rename one graph folder or exclude it explicitly.'
            )
        used_ids[gid] = folder.name
        results.append((folder.name, folder, gid))
    if not results:
        raise RuntimeError(f'No valid Logseq DB graphs containing db.sqlite were found in {root}; refusing to publish an empty manifest')
    return results


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def publish_all(staged_targets: list[tuple[Path, Path]], rollback_root: Path) -> None:
    """Publish validated files, and rollback the whole set on normal errors.

    Each move is atomic on a single filesystem, but multiple files cannot form
    one crash-atomic transaction. Manifest is installed last by the caller.
    """
    rollback_root.mkdir(parents=True, exist_ok=False)
    entries: list[tuple[Path, Path, bool]] = []
    try:
        for index, (staged, destination) in enumerate(staged_targets):
            if not staged.exists():
                raise RuntimeError(f'Staged backup vanished: {staged}')
            destination.parent.mkdir(parents=True, exist_ok=True)
            previous = rollback_root / str(index)
            had_previous = destination.exists()
            if had_previous:
                os.replace(destination, previous)
            entries.append((destination, previous, had_previous))
            os.replace(staged, destination)
    except Exception:
        for destination, previous, had_previous in reversed(entries):
            if destination.exists():
                _remove(destination)
            if had_previous and previous.exists():
                os.replace(previous, destination)
        raise
    finally:
        # If cleanup fails after publishing, allow the next run to continue.
        shutil.rmtree(rollback_root, ignore_errors=True)


def run_multi(cfg: dict, repo: Path, *, snapshot_api) -> int:
    """Main per-graph pipeline. snapshot_api is the existing snapshot module."""
    if cfg.get('auto_discover_graphs', True) is not True:
        raise RuntimeError('Multi-graph mode requires auto_discover_graphs=true')
    root_raw = cfg.get('source_graphs_dir')
    if not isinstance(root_raw, str) or not root_raw.strip():
        raise RuntimeError('source_graphs_dir must name the parent Logseq graphs directory')
    root_unresolved = Path(root_raw).expanduser()
    if root_unresolved.is_symlink():
        raise RuntimeError('source_graphs_dir must not be a symbolic link')
    root = root_unresolved.resolve()
    if repo == root or repo in root.parents or root in repo.parents:
        raise RuntimeError('Logseq source folder must be outside the Git backup repository')
    found = discover_graphs(root, cfg.get('exclude_graphs', []))
    print(f'[discover] Found {len(found)} graph(s): ' + ', '.join(x[0] for x in found), flush=True)

    snapshot_cfg = cfg.get('snapshot', {})
    mobile_cfg = cfg.get('mobile_export', {})
    if not isinstance(snapshot_cfg, dict) or not isinstance(mobile_cfg, dict):
        raise RuntimeError('snapshot and mobile_export must be JSON objects')
    mobile_enabled = bool(mobile_cfg.get('enabled', False))
    if mobile_enabled and not mobile_cfg.get('required', True):
        raise RuntimeError('Multi-graph exports require mobile_export.required=true to prevent mismatched JSON and database versions')

    data = repo / 'data'
    data.mkdir(exist_ok=True)
    maximum = float(snapshot_cfg.get('max_file_mib', 95))
    if mobile_enabled:
        mode = mobile_cfg.get('mode', 'edn-cli')
        if mode not in ('edn-cli', 'page-tree-cli'):
            raise RuntimeError('mobile_export.mode must be edn-cli or page-tree-cli')
    manifest_rows = []
    with tempfile.TemporaryDirectory(prefix='.logseq-stage-', dir=data) as td:
        stage = Path(td)
        targets: list[tuple[Path, Path]] = []
        for index, (name, source, gid) in enumerate(found, start=1):
            print(f'[{index}/{len(found)}] Graph: {name}', flush=True)
            graph_stage = stage / 'graphs' / name
            graph_stage.mkdir(parents=True)
            db_stage = graph_stage / 'db.sqlite'
            provenance, _ = snapshot_api.snapshot_with_native_fallback(
                source, db_stage, snapshot_cfg, data / name / 'db.sqlite'
            )
            print(f'[ok] SQLite snapshot validated ({db_stage.stat().st_size:,} bytes; {provenance})', flush=True)
            snapshot_api.copy_assets_if_present(source, graph_stage, bool(snapshot_cfg.get('include_assets', True)))
            snapshot_api.enforce_size_limits([graph_stage], maximum)
            targets.append((graph_stage, data / name))

            if mobile_enabled:
                outfile = stage / 'mobile' / f'{gid}.json'
                outfile.parent.mkdir(parents=True, exist_ok=True)
                if mode == 'edn-cli':
                    from cli_edn_export import export_snapshot_to_json
                    result = export_snapshot_to_json(
                        cli_command=mobile_cfg.get('cli_command', ['logseq']),
                        isolated_root=stage,
                        graph_name=name,
                        destination=outfile,
                        timeout=int(mobile_cfg.get('timeout_seconds', 120)),
                        cli_style=mobile_cfg.get('cli_style', 'auto'),
                    )
                else:
                    from export_json import export_with_cli
                    result = export_with_cli(
                        cli_command=mobile_cfg.get('cli_command', ['logseq']),
                        isolated_root=stage, graph_name=name, destination=outfile,
                        timeout=int(mobile_cfg.get('timeout_seconds', 120)),
                    )
                    result = json.loads(outfile.read_text(encoding='utf-8'))
                if result.get('graph') != name:
                    raise RuntimeError(f'Exported JSON graph mismatch for {name}: {result.get("graph")!r}')
                if not isinstance(result.get('pageCount'), int) or not isinstance(result.get('blockCount'), int):
                    raise RuntimeError(f'Mobile JSON missing expected page/block counts for {name}')
                snapshot_api.enforce_size_limits([outfile], maximum)
                manifest_rows.append({
                    'id': gid,
                    'name': name,
                    'file': f'{gid}.json',
                    'path': f'data/mobile/{gid}.json',
                    'pageCount': result['pageCount'],
                    'blockCount': result['blockCount'],
                    'sha256': hashlib.sha256(outfile.read_bytes()).hexdigest(),
                })
                targets.append((outfile, data / 'mobile' / f'{gid}.json'))
                print(f'[ok] Mobile JSON ready: {name}: {result["pageCount"]} pages / {result["blockCount"]} blocks', flush=True)

        if mobile_enabled:
            manifest = stage / 'mobile' / 'manifest.json'
            manifest.write_text(json.dumps({'schemaVersion': 1, 'graphs': manifest_rows},
                                           ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            snapshot_api.enforce_size_limits([manifest], maximum)
            targets.append((manifest, data / 'mobile' / 'manifest.json'))  # manifest last
        else:
            print('[warning] Mobile export disabled; manifest is not modified.', flush=True)
        print('[publish] All graphs validated; publishing backup files', flush=True)
        publish_all(targets, stage / 'rollback')

    status = {
        'graphs': [dict(id=gid, name=name) for name, _, gid in found],
        'database': 'validated',
        'mobile_export': 'updated' if mobile_enabled else 'disabled',
    }
    snapshot_api.write_status(status)
    print(f'[ok] Published {len(found)} graph(s)' + (' and manifest.json' if mobile_enabled else ''), flush=True)
    return 0
