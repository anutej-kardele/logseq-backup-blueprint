#!/usr/bin/env python3
"""Hold an exclusive local POSIX advisory lock throughout a logpush run.

Works on macOS and Linux; prevents overlapping snapshots AND Git operations.
"""
from __future__ import annotations

import fcntl
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print('Usage: with_lock.py LOCKFILE COMMAND [ARG...]', file=sys.stderr)
        return 2
    lockfile = Path(sys.argv[1])
    lockfile.parent.mkdir(parents=True, exist_ok=True)
    with lockfile.open('a+') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('[error] Another logpush backup is already running. Try again when it completes.', file=sys.stderr)
            return 1
        env = os.environ.copy()
        env['LOGSEQ_BACKUP_LOCK_HELD'] = '1'
        try:
            return subprocess.call(sys.argv[2:], env=env)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


if __name__ == '__main__':
    raise SystemExit(main())
