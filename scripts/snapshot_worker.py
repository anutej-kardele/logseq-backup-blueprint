#!/usr/bin/env python3
"""Short-lived process for SQLite online backup; parent enforces a hard timeout."""
from __future__ import annotations
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
from urllib.parse import quote


def snapshot(source: Path, dest: Path) -> None:
    uri = 'file:' + quote(str(source), safe='/') + '?mode=ro'
    dest.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(uri, uri=True, timeout=2)) as reader:
        with closing(sqlite3.connect(str(dest), timeout=2)) as writer:
            reader.backup(writer, pages=128, sleep=0.1)
            check = writer.execute('PRAGMA integrity_check').fetchone()
            if not check or check[0] != 'ok':
                raise RuntimeError(f'Integrity check failed: {check}')
        # Publish standalone file in DELETE mode without lingering WAL/SHM files.
        with closing(sqlite3.connect(str(dest), timeout=2)) as standalone:
            mode = standalone.execute('PRAGMA journal_mode=DELETE').fetchone()
            if not mode or mode[0].lower() != 'delete':
                raise RuntimeError(f'Could not set snapshot journal mode to DELETE: {mode}')
            check = standalone.execute('PRAGMA integrity_check').fetchone()
            if not check or check[0] != 'ok':
                raise RuntimeError(f'Final integrity check failed: {check}')
    if dest.stat().st_size == 0:
        raise RuntimeError('Backup is empty')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('Usage: snapshot_worker.py SOURCE_SQLITE DEST_SQLITE')
    try:
        snapshot(Path(sys.argv[1]), Path(sys.argv[2]))
    except (RuntimeError, OSError, sqlite3.Error) as exc:
        print(f'[error] {exc}', file=sys.stderr)
        raise SystemExit(1) from exc
