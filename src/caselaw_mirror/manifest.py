"""Manifest: tracks url -> local file state so re-runs can skip unchanged content.

Backed by SQLite, sharded one file per reporter (`<dir>/<reporter_slug>.sqlite3`,
root-level metadata in `<dir>/_root.sqlite3`) — reporters range from a
handful of volumes to 220+ (California), so this is a bounded, human-
navigable unit rather than one enormous file for ~6.7M case rows, or an
explosion of one-file-per-volume shards.

SQLite is the fast day-to-day backend; `export_jsonl`/`import_jsonl` convert
to/from a single sorted, line-per-file JSONL artifact that diffs cleanly if
anyone wants to commit or review a manifest snapshot (e.g. in a future CI
run), without requiring SQLite as the "committable" format.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Protocol, Self
from urllib.parse import urlparse


@dataclass
class ManifestEntry:
    url: str
    local_path: str
    format: str
    etag: str | None = None
    last_modified: str | None = None
    sha256: str | None = None  # published checksum verification (tar bundles) when applicable
    git_blob_sha1: str | None = None  # fallback content identity for every other format
    size_bytes: int | None = None
    status: str = "pending"  # ok | checksum_failed | http_error | pending
    first_fetched_at: str | None = None
    last_checked_at: str | None = None
    last_changed_at: str | None = None


class ManifestStore(Protocol):
    def get(self, url: str) -> ManifestEntry | None: ...

    def upsert(self, entry: ManifestEntry) -> None: ...


_ENTRY_FIELDS = tuple(f.name for f in fields(ManifestEntry))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    url TEXT PRIMARY KEY,
    local_path TEXT NOT NULL,
    format TEXT NOT NULL,
    etag TEXT,
    last_modified TEXT,
    sha256 TEXT,
    git_blob_sha1 TEXT,
    size_bytes INTEGER,
    status TEXT NOT NULL,
    first_fetched_at TEXT,
    last_checked_at TEXT,
    last_changed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_format ON files(format);
"""

ROOT_SHARD_NAME = "_root"


def shard_key_for_url(url: str) -> str:
    """First path segment of a static.case.law URL: the reporter slug, or
    `_root` for the handful of top-level metadata files."""
    from caselaw_mirror.layout import ROOT_METADATA_FILES

    path = urlparse(url).path.lstrip("/")
    first_segment = path.split("/", 1)[0] if path else ""
    if not first_segment or first_segment in ROOT_METADATA_FILES:
        return ROOT_SHARD_NAME
    return first_segment


class SqliteManifestShard:
    """A single reporter's (or `_root`'s) manifest, backed by one SQLite file."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def get(self, url: str) -> ManifestEntry | None:
        row = self._conn.execute(
            f"SELECT {', '.join(_ENTRY_FIELDS)} FROM files WHERE url = ?", (url,)
        ).fetchone()
        if row is None:
            return None
        return ManifestEntry(**dict(zip(_ENTRY_FIELDS, row, strict=True)))

    def upsert(self, entry: ManifestEntry) -> None:
        data = asdict(entry)
        columns = ", ".join(_ENTRY_FIELDS)
        placeholders = ", ".join(f":{f}" for f in _ENTRY_FIELDS)
        updates = ", ".join(f"{f} = excluded.{f}" for f in _ENTRY_FIELDS if f != "url")
        self._conn.execute(
            f"INSERT INTO files ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(url) DO UPDATE SET {updates}",
            data,
        )
        self._conn.commit()

    def all_entries(self) -> list[ManifestEntry]:
        rows = self._conn.execute(f"SELECT {', '.join(_ENTRY_FIELDS)} FROM files").fetchall()
        return [ManifestEntry(**dict(zip(_ENTRY_FIELDS, row, strict=True))) for row in rows]


class ShardedManifest:
    """ManifestStore implementation that routes each url to its reporter's
    SQLite shard, opening shard files lazily and caching the connections."""

    def __init__(self, manifest_dir: str | Path):
        self._dir = Path(manifest_dir)
        self._shards: dict[str, SqliteManifestShard] = {}

    def _shard_for(self, url: str) -> SqliteManifestShard:
        key = shard_key_for_url(url)
        if key not in self._shards:
            self._shards[key] = SqliteManifestShard(self._dir / f"{key}.sqlite3")
        return self._shards[key]

    def get(self, url: str) -> ManifestEntry | None:
        return self._shard_for(url).get(url)

    def upsert(self, entry: ManifestEntry) -> None:
        self._shard_for(entry.url).upsert(entry)

    def close(self) -> None:
        for shard in self._shards.values():
            shard.close()
        self._shards.clear()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def iter_shard_paths(self) -> list[Path]:
        if not self._dir.is_dir():
            return []
        return sorted(self._dir.glob("*.sqlite3"))

    def all_entries(self) -> list[ManifestEntry]:
        entries: list[ManifestEntry] = []
        for path in self.iter_shard_paths():
            key = path.stem
            shard = self._shards.get(key) or SqliteManifestShard(path)
            self._shards[key] = shard
            entries.extend(shard.all_entries())
        return entries


def export_jsonl(manifest_dir: str | Path, output_path: str | Path) -> int:
    """Write every entry across all shards to one JSONL file, sorted by url
    for deterministic diffs. Returns the number of entries written."""
    manifest = ShardedManifest(manifest_dir)
    entries = sorted(manifest.all_entries(), key=lambda e: e.url)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        for entry in entries:
            f.write(json.dumps(asdict(entry), sort_keys=True))
            f.write("\n")
    return len(entries)


def import_jsonl(input_path: str | Path, manifest_dir: str | Path) -> int:
    """Load a JSONL manifest snapshot into the sharded SQLite store. Returns
    the number of entries imported."""
    manifest = ShardedManifest(manifest_dir)
    count = 0
    with Path(input_path).open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            manifest.upsert(ManifestEntry(**{k: data.get(k) for k in _ENTRY_FIELDS}))
            count += 1
    manifest.close()
    return count
