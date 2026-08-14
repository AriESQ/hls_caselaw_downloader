"""Manifest: tracks url -> local file state so re-runs can skip unchanged content.

`ManifestEntry` and the `ManifestStore` protocol are defined here first since
downloader.py depends on them; the concrete SQLite/JSONL-backed store is
filled in alongside this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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
