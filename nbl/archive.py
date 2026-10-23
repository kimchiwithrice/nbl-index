"""Keeps a copy of everything the build reads and what the model said, in a separate data folder.

Layout of the archive folder:
    nblr/<table>/<season>.csv     nblR's files split by season (only the seasons that changed get rewritten)
    nblr/pbp/<season>/<match>.csv play-by-play, one file per game since a season of it is around 45 MB
    feed/<season>/<match_id>.json the raw NBL feed for every game the fallback read
    snapshots/<season>/<date>.json ratings, season sim, predictions and player values (one per Sydney day)
    ledger.json                   the prediction ledger
    sources.json                  checksums of the source files last archived, so unchanged ones are skipped

Nothing is ever deleted from the archive: if a source goes missing or drops a season, the
copy already saved stays as it is. The folder is meant to be a git repo, so older versions of
each file are kept in its history.
"""
import glob
import hashlib
import json
import os
import shutil

import numpy as np
import pandas as pd

from . import rds

TABLES = {"results": "results_wide.csv", "box_team": "box_team.csv", "box_player": "box_player.csv", "pbp": "pbp.rds"}


def save(archive_dir, data_dir, snapshot=None, log=print):
    os.makedirs(archive_dir, exist_ok=True)
    sources_path = os.path.join(archive_dir, "sources.json")
    sources = _read_json(sources_path) or {}
    changed = 0
    for table, name in TABLES.items():
        path = os.path.join(data_dir, name)
        if not os.path.exists(path):
            continue
        digest = _md5(path)
        if sources.get(table) == digest:
            continue
        df = rds.read_frame(path) if name.endswith(".rds") else pd.read_csv(path, low_memory=False)
        changed += _write_split(df, os.path.join(archive_dir, "nblr", table), by_game=(table == "pbp"))
        sources[table] = digest
    _write_json(sources_path, sources)

    feed = 0
    for path in glob.glob(os.path.join(data_dir, "feed", "*", "*.json")):
        season = os.path.basename(os.path.dirname(path))
        dest = os.path.join(archive_dir, "feed", season, os.path.basename(path))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(path, dest)
        feed += 1

    ledger = os.path.join(data_dir, "ledger.json")
    if os.path.exists(ledger):
        shutil.copyfile(ledger, os.path.join(archive_dir, "ledger.json"))

    if snapshot:
        dest = os.path.join(archive_dir, "snapshots", snapshot["season"], snapshot["date"] + ".json")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        _write_json(dest, snapshot)
    log(f"Archive: {changed} file(s) updated, {feed} feed game(s), "
        f"snapshot {'saved' if snapshot else 'skipped'}")


def _write_split(df, folder, by_game=False):
    """One CSV per season (or per game). A file is only rewritten when its contents change."""
    df = df.copy()
    for col in df.columns:  # list columns from the RDS file can't go into a CSV as they are
        if df[col].dtype == object:
            df[col] = df[col].map(lambda v: ";".join(map(str, v)) if isinstance(v, (list, tuple)) else v)
    keys = [df["season"].astype(str)] + ([df["match_id"].astype(str)] if by_game else [])
    written = 0
    for key, rows in df.groupby(keys, sort=True):
        key = key if isinstance(key, tuple) else (key,)
        path = os.path.join(folder, *key[:-1], key[-1] + ".csv")
        text = rows.to_csv(index=False)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                if f.read() == text:
                    continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        written += 1
    return written


def plain(obj):
    """Numbers, arrays and frames from the model turned into things json can write."""
    if isinstance(obj, dict):
        return {str(k): plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [plain(v) for v in obj]
    if isinstance(obj, pd.Series):
        return plain(obj.to_dict())
    if isinstance(obj, np.ndarray):
        return plain(obj.tolist())
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return None if np.isnan(obj) else round(float(obj), 4)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if isinstance(obj, (str, int, bool)) or obj is None:
        return obj
    return str(obj)


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, sort_keys=True)
        f.write("\n")
