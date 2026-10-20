#!/usr/bin/env python3
"""Export Logseq Build EDN from a staged SQLite graph, then generate mobile JSON.

Only the ISOLATED staged graph is passed to Logseq CLI. The live graph is never
handed to the CLI. Two CLI generations are supported:
  modern: logseq --root-dir DIR --graph NAME graph export --type edn --file FILE
  legacy: @logseq/cli 0.4.x: logseq export-edn -g PATH_TO_SNAPSHOT -f FILE

Both the :datoms and :pages-and-blocks formats are validated and converted.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
import signal
import subprocess
import tempfile

from edn_to_json import convert_file


def run_bounded(command: list[str], *, timeout: int, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Bound a CLI invocation; terminate its process group if it hangs."""
    try:
        proc = subprocess.Popen(
            command, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Logseq CLI executable {command[0]!r} not found. "
            "Install a supported Logseq CLI and verify `logseq --help`."
        ) from exc
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        # Terminate children as well, including a CLI-started local worker.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()
        raise RuntimeError(f"Logseq CLI timed out after {timeout}s") from exc
    return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)


def checked(command: list[str], *, timeout: int, cwd: Path) -> str:
    result = run_bounded(command, timeout=timeout, cwd=cwd)
    if result.returncode != 0:
        message = ((result.stderr or '') + '\n' + (result.stdout or '')).strip()
        raise RuntimeError(
            f"Logseq CLI exited {result.returncode}: {message[:1200] or 'no error details'}"
        )
    return result.stdout


def choose_style(command: list[str], *, timeout: int, cwd: Path, selected: str) -> str:
    if selected in ('modern', 'legacy'):
        return selected
    if selected != 'auto':
        raise ValueError('mobile_export.cli_style must be auto, modern, or legacy')
    help_text = checked([*command, '--help'], timeout=min(15, timeout), cwd=cwd)
    # The new CLI exposes 'graph', the published 0.4.x npm CLI exposes export-edn.
    if re.search(r'(?m)^\s*graph(?:\s|$)', help_text):
        return 'modern'
    if 'export-edn' in help_text:
        return 'legacy'
    raise RuntimeError('Could not determine Logseq CLI generation from --help; set mobile_export.cli_style explicitly')


def export_snapshot_to_json(
    *, cli_command: list[str], isolated_root: Path, graph_name: str,
    destination: Path, timeout: int, cli_style: str = 'auto',
) -> dict:
    if isinstance(cli_command, str):
        cli_command = [cli_command]
    if not isinstance(cli_command, list) or not cli_command or not all(
        isinstance(x, str) and x for x in cli_command
    ):
        raise ValueError('mobile_export.cli_command must contain executable name and optional arguments')
    db_file = isolated_root / 'graphs' / graph_name / 'db.sqlite'
    if not db_file.is_file():
        raise RuntimeError(f'Isolated database missing: {db_file}')
    if timeout < 1:
        raise ValueError('mobile_export.timeout_seconds must be positive')

    style = choose_style(cli_command, timeout=timeout, cwd=isolated_root, selected=cli_style)
    print(f'[info] Logseq CLI export mode: {style}', flush=True)
    with tempfile.TemporaryDirectory(prefix='logseq-edn-', dir=isolated_root) as edn_dir:
        edn = Path(edn_dir) / 'activity.edn'
        if style == 'modern':
            cmd = [*cli_command, '--root-dir', str(isolated_root), '--graph', graph_name,
                   '--timeout-ms', str(min(120000, timeout * 1000)),
                   'graph', 'export', '--type', 'edn', '--file', str(edn)]
        else:
            # The published @logseq/cli 0.4.x accepts direct paths to SQLite graphs.
            # Using the staged file prevents opening the user's live graph.
            cmd = [*cli_command, 'export-edn', '-g', str(db_file), '-f', str(edn)]
        try:
            checked(cmd, timeout=timeout, cwd=isolated_root)
            if not edn.is_file() or edn.stat().st_size < 40:
                raise RuntimeError('Logseq CLI did not produce a nonempty EDN export')
            result = convert_file(edn, destination, graph_name)
            if result['pageCount'] < 1:
                raise RuntimeError('EDN JSON conversion produced no pages')
            print(f"[ok] Mobile JSON: {result['pageCount']} pages / {result['blockCount']} blocks", flush=True)
            return result
        finally:
            if style == 'modern':
                # Prevent CLI-managed server from keeping the ephemeral staging root open.
                stop = [*cli_command, '--root-dir', str(isolated_root), '--graph', graph_name,
                        'server', 'stop', '--graph', graph_name]
                try:
                    run_bounded(stop, timeout=min(10, timeout), cwd=isolated_root)
                except (RuntimeError, OSError):
                    # Cleanup is best-effort; never mask the actual export error.
                    pass
