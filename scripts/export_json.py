#!/usr/bin/env python3
"""Create a read-only mobile JSON export with the official Logseq CLI.

Logseq DB SQLite stores serialized DataScript, not SQL tables of notes.
Do not try to dump 'kvs' or guess page structure. We query an ISOLATED copy
of the graph with the official Logseq CLI and retain its structured tree.

CLI must be separately installed. Until then, snapshots still work and this
module fails explicitly; it never creates an empty/misleading notes.json.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


def cli_call(command: list[str], root: Path, graph: str, args: list[str], timeout: int) -> dict:
    invocation = [*command, "--root-dir", str(root), "--graph", graph, "--output", "json", *args]
    try:
        completed = subprocess.run(
            invocation, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=timeout, check=False, cwd=root,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Logseq CLI executable '{command[0]}' not installed. "
            "See README: optional CLI setup."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Logseq CLI timed out after {timeout}s running {args!r}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[:800]
        raise RuntimeError(f"Logseq CLI failed with exit {completed.returncode}: {detail}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Logseq CLI did not return JSON. Verify a current DB-version CLI "
            f"and the --output json option. Output: {completed.stdout[:250]!r}"
        ) from exc
    if not isinstance(result, dict):
        raise RuntimeError(f"Unexpected Logseq CLI response type for {args!r}")
    if result.get("ok") is False or result.get("success") is False:
        raise RuntimeError(f"Logseq CLI reported an error: {str(result)[:500]}")
    return result


def get_data(response: dict) -> dict:
    data = response.get("data", response)
    if not isinstance(data, dict):
        raise RuntimeError("Logseq CLI 'data' is not a JSON object")
    return data


def list_pages(response: dict) -> list[dict]:
    data = get_data(response)
    items = data.get("items")
    if not isinstance(items, list):
        raise RuntimeError("Logseq CLI list page output does not contain data.items[]")
    result = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise RuntimeError("Unexpected non-object page in CLI output")
        name = item.get("block/name") or item.get("block/title") or item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise RuntimeError(f"Page missing name/title; CLI schema might have changed: {str(item)[:250]}")
        if name not in seen:
            result.append({"name": name, "metadata": item})
            seen.add(name)
    if not result:
        raise RuntimeError("Logseq CLI returned no pages; refusing to replace a prior export")
    result.sort(key=lambda p: p["name"].casefold())
    return result


def export_with_cli(
    cli_command: list[str], isolated_root: Path, graph_name: str,
    destination: Path, timeout: int = 120,
) -> None:
    if isinstance(cli_command, str):
        cli_command = [cli_command]
    if not isinstance(cli_command, list) or not cli_command or not all(
        isinstance(part, str) and part for part in cli_command
    ):
        raise RuntimeError("mobile_export.cli_command must be a non-empty array of command strings")
    if not (isolated_root / "graphs" / graph_name / "db.sqlite").is_file():
        raise RuntimeError("Cannot export: isolated SQLite snapshot is missing")
    pages = list_pages(cli_call(cli_command, isolated_root, graph_name, ["list", "page"], timeout))
    documents = []
    for idx, page in enumerate(pages, start=1):
        response = cli_call(cli_command, isolated_root, graph_name,
                            ["show", "--page", page["name"]], timeout)
        data = get_data(response)
        # Current CLI documents a data.root page tree. Refuse to replace an
        # existing good export if a new CLI changes this response shape.
        root = data.get("root")
        if not isinstance(root, dict):
            raise RuntimeError(
                f"CLI show output for {page['name']!r} did not contain data.root object"
            )
        documents.append({"name": page["name"], "metadata": page["metadata"], "root": root})
        if idx % 25 == 0:
            print(f"       Exported {idx}/{len(pages)} pages")

    content = {
        "schemaVersion": 1,
        "source": "logseq-cli-read-only",
        "graph": graph_name,
        "pageCount": len(documents),
        "pages": documents,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(destination.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as out:
            json.dump(content, out, indent=2, ensure_ascii=False, sort_keys=True)
            out.write("\n")
        # Validate the serialized file before promotion.
        with tmp.open(encoding="utf-8") as inp:
            parsed = json.load(inp)
        if parsed["pageCount"] != len(parsed["pages"]):
            raise RuntimeError("Unexpected JSON export length mismatch")
        os.replace(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)
