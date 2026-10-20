#!/usr/bin/env python3
"""Consistent, validated SQLite snapshot + optional Logseq CLI mobile export.

No third-party Python packages. Never writes to the original Logseq directory.
"""
from __future__ import annotations

import argparse
import json
from contextlib import closing
import os
import shutil
import signal
import subprocess
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[1]


def config_from(path: Path) -> dict:
    if not path.is_file():
        raise RuntimeError(f"Missing {path}. Copy config.example.json to config.json and edit it.")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"Unable to read configuration: {exc}") from exc
    if not isinstance(config, dict):
        raise RuntimeError("Configuration must be a JSON object")
    return config


def sqlite_snapshot(source: Path, destination: Path, timeout_seconds: int = 30) -> None:
    """Create a validated snapshot in a child process with a hard timeout.

    SQLite's backup() API can block indefinitely on an EXCLUSIVE source lock.
    Killing our child process leaves the original graph completely untouched.
    """
    if not 1 <= timeout_seconds <= 3600:
        raise ValueError('snapshot.timeout_seconds must be between 1 and 3600')
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-S', str(REPO / 'scripts' / 'snapshot_worker.py'),
               str(source), str(destination)]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, stdin=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        # Kill only this project's child process group, never Logseq itself.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
        raise RuntimeError(
            f'SQLite snapshot timed out after {timeout_seconds}s. '
            'Logseq may be holding an exclusive lock. Previous backup was preserved.'
        ) from exc
    if process.returncode != 0:
        raise RuntimeError(
            'SQLite snapshot failed: ' + (stderr or stdout or 'Unknown SQLite error').strip()[:1000]
        )
    if not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError('SQLite snapshot worker did not create a usable database')



def native_backup_candidates(graph_dir: Path, max_age_minutes: int) -> list[Path]:
    """Return newest recent completed SQLite files from Logseq-managed backup dirs.

    Native backups are created through Logseq's own worker, which can access its
    exclusive live connection. These files are read only; we never modify them.
    """
    if not 1 <= max_age_minutes <= 10080:
        raise ValueError('snapshot.native_backup_max_age_minutes must be 1..10080')
    now = time.time()
    candidates = []
    for folder_name in ('backup', 'backups'):
        root = graph_dir / folder_name
        if not root.is_dir() or root.is_symlink():
            continue
        for path in root.rglob('*'):
            if len(path.relative_to(root).parts) > 4:
                continue
            if path.name != 'db.sqlite' and not (path.name.endswith('.sqlite') and not path.name.endswith(('-wal', '-shm'))):
                continue
            if not path.is_file() or path.is_symlink() or any(a.is_symlink() for a in path.parents if a != root and root in a.parents):
                continue
            try:
                age = now - path.stat().st_mtime
                if -300 <= age <= max_age_minutes * 60 and path.stat().st_size > 0:
                    candidates.append(path)
            except OSError:
                continue
    return sorted(candidates, key=lambda file: file.stat().st_mtime, reverse=True)[:12]


def snapshot_with_native_fallback(source: Path, dest: Path, cfg: dict, last_published: Path) -> tuple[str, float]:
    """Return provenance and content freshness timestamp; never publish older data.

    If Logseq holds its exclusive DB lock, only take a recent, fully validated
    Logseq-created backup. Reject any candidate older than our published snapshot.
    The mtime guard is intentionally conservative; it may defer an update, but
    it won't roll the current backup back to older notes.
    """
    timeout = int(cfg.get('timeout_seconds', 30))
    try:
        sqlite_snapshot(source / 'db.sqlite', dest, timeout)
        return ('live', time.time())
    except RuntimeError as exc:
        reason = str(exc).lower()
        if not cfg.get('fallback_to_native_backup', True) or not any(
            word in reason for word in ('timed out', 'database is locked', 'database is busy')
        ):
            raise
        print(f'[warning] Cannot snapshot live SQLite: {exc}', flush=True)

    candidates = native_backup_candidates(source, int(cfg.get('native_backup_max_age_minutes', 120)))
    last_time = last_published.stat().st_mtime if last_published.exists() else 0
    for candidate in candidates:
        backup_mtime = candidate.stat().st_mtime
        if backup_mtime < last_time - 2:
            print('[warning] Logseq native backups are older than the last published backup; refusing to roll back data.', flush=True)
            break
        try:
            if dest.exists():
                dest.unlink()
            sqlite_snapshot(candidate, dest, min(timeout, 10))
            print(f'[info] Used Logseq native snapshot: {candidate}', flush=True)
            print(f'[info] Native backup age: {round((time.time()-backup_mtime)/60, 1)} minutes (may omit newer notes)', flush=True)
            return ('logseq-native-backup', backup_mtime)
        except (OSError, RuntimeError) as exc:
            print(f'[warning] Skipping invalid/busy native backup {candidate.name}: {exc}', flush=True)
    raise RuntimeError(
        'Live database is locked; no sufficiently recent, newer, validated Logseq native backup exists. '
        'Previous GitHub/local snapshot was preserved. Quit Logseq and retry, or wait for a new native backup.'
    )

def copy_assets_if_present(source: Path, destination: Path, enabled: bool) -> None:
    if not enabled:
        return
    assets = source / "assets"
    if not assets.exists():
        print("[info] No assets/ directory found; continuing (may be normal).")
        return
    if not assets.is_dir() or assets.is_symlink():
        raise RuntimeError("assets must be a normal directory, not a symlink")
    # Prevent following symlinks that could accidentally copy files outside the graph.
    for path in assets.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"Refusing to follow asset symlink: {path}")
    shutil.copytree(assets, destination / "assets")


def enforce_size_limits(paths: list[Path], max_mib: float) -> None:
    maximum = int(max_mib * 1024 * 1024)
    if not 0 < maximum < 100 * 1024 * 1024:
        raise RuntimeError("snapshot.max_file_mib must be greater than 0 and below 100")
    for item in paths:
        files = item.rglob("*") if item.is_dir() else [item]
        for file in files:
            if file.is_file() and file.stat().st_size > maximum:
                raise RuntimeError(
                    f"{file.name} is {file.stat().st_size / 1048576:.1f} MiB, "
                    f"above configured limit {max_mib} MiB. GitHub is not suitable for this file."
                )


def publish_directory(staged: Path, target: Path) -> None:
    """Publish completed folder; restore previous folder if rename fails."""
    target.parent.mkdir(parents=True, exist_ok=True)
    old = target.parent / f".{target.name}-previous-{os.getpid()}"
    if old.exists():
        raise RuntimeError(f"Stale temporary directory exists: {old}")
    moved_old = False
    try:
        if target.exists():
            target.rename(old)
            moved_old = True
        staged.rename(target)
    except Exception:
        if moved_old and old.exists() and not target.exists():
            old.rename(target)
        raise
    if moved_old:
        shutil.rmtree(old)


def write_status(status: dict) -> None:
    (REPO / ".backup-status.json").write_text(
        json.dumps(status, indent=2) + "\n", encoding="utf-8"
    )


def run(config_path: Path) -> int:
    cfg = config_from(config_path)
    if 'source_graphs_dir' in cfg:
        from multi_graph import run_multi
        return run_multi(cfg, REPO, snapshot_api=sys.modules[__name__])
    source = Path(str(cfg.get("source_graph_dir", ""))).expanduser().resolve()
    graph = cfg.get("graph_name", source.name)
    if not isinstance(graph, str) or not graph or graph in {".", ".."} or "/" in graph or "\\" in graph:
        raise RuntimeError("graph_name must be one folder name")
    if not (source / "db.sqlite").is_file():
        raise RuntimeError(f"Logseq SQLite database not found at {source / 'db.sqlite'}")
    if REPO == source or REPO in source.parents or source in REPO.parents:
        raise RuntimeError("The original graph must be outside the backup repository")

    snapshot_cfg = cfg.get("snapshot", {})
    mobile_cfg = cfg.get("mobile_export", {})
    max_mib = float(snapshot_cfg.get("max_file_mib", 95))
    data_dir = REPO / "data"
    data_dir.mkdir(exist_ok=True)
    status = {"graph": graph, "database": "not completed", "mobile_export": "not requested"}
    with tempfile.TemporaryDirectory(prefix=".logseq-stage-", dir=data_dir) as stage_string:
        stage = Path(stage_string)
        graph_stage = stage / "graphs" / graph
        graph_stage.mkdir(parents=True)
        db_stage = graph_stage / "db.sqlite"
        print(f"[1/4] Consistent SQLite backup: {source / 'db.sqlite'}")
        capture_method, capture_time = snapshot_with_native_fallback(
            source, db_stage, snapshot_cfg, data_dir / graph / 'db.sqlite'
        )
        status['snapshot_source'] = capture_method
        status['snapshot_timestamp'] = capture_time
        print(f"[ok] SQLite snapshot validated ({db_stage.stat().st_size:,} bytes)")
        copy_assets_if_present(source, graph_stage, bool(snapshot_cfg.get("include_assets", True)))
        status["database"] = "validated"

        output_name = f"{graph.casefold()}.json"
        mobile_stage = stage / output_name
        mobile_ok = False
        if mobile_cfg.get("enabled", False):
            mode = mobile_cfg.get('mode', 'edn-cli')
            print(f"[2/4] Exporting read-only mobile JSON ({mode})", flush=True)
            try:
                if mode == 'edn-cli':
                    from cli_edn_export import export_snapshot_to_json
                    export_snapshot_to_json(
                        cli_command=mobile_cfg.get('cli_command', ['logseq']),
                        isolated_root=stage,
                        graph_name=graph,
                        destination=mobile_stage,
                        timeout=int(mobile_cfg.get('timeout_seconds', 120)),
                        cli_style=mobile_cfg.get('cli_style', 'auto'),
                    )
                elif mode == 'page-tree-cli':
                    from export_json import export_with_cli
                    export_with_cli(
                        cli_command=mobile_cfg.get('cli_command', ['logseq']),
                        isolated_root=stage,
                        graph_name=graph,
                        destination=mobile_stage,
                        timeout=int(mobile_cfg.get('timeout_seconds', 120)),
                    )
                else:
                    raise ValueError('mobile_export.mode must be edn-cli or page-tree-cli')
                mobile_ok = True
                status['mobile_export'] = 'updated'
                print('[ok] Mobile export generated and validated', flush=True)
            except Exception as exc:
                status['mobile_export'] = f'skipped: {exc}'
                print(f'[warning] Mobile export not updated: {exc}', file=sys.stderr, flush=True)
                if mobile_cfg.get('required', True):
                    write_status(status)
                    raise RuntimeError(
                        'Mobile export is required and failed; existing DB and JSON were preserved'
                    ) from exc
        else:
            print('[2/4] Mobile export disabled in config', flush=True)
            status['mobile_export'] = 'disabled'

        size_paths = [graph_stage]
        if mobile_ok:
            size_paths.append(mobile_stage)
        enforce_size_limits(size_paths, max_mib)
        print("[3/4] Publishing latest validated database snapshot")
        publish_directory(graph_stage, data_dir / graph)
        if mobile_ok:
            (data_dir / "mobile").mkdir(exist_ok=True)
            os.replace(mobile_stage, data_dir / "mobile" / output_name)
        print("[4/4] Local backup complete")
    write_status(status)
    print(f"[status] {status['mobile_export']}")
    print(f"[path] {data_dir / graph / 'db.sqlite'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=REPO / "config.json")
    args = parser.parse_args()
    try:
        return run(args.config)
    except (RuntimeError, OSError, sqlite3.Error, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
