"""Executes a resolved file plan: conditional fetch, checksum verify, atomic write.

Designed so a hung run is diagnosable from the logs alone: every file
transition logs at DEBUG, and a periodic INFO heartbeat rolls up running
counts (requirement: "decent visibility... not just silently hung").
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from hls_caselaw_downloader import checksum
from hls_caselaw_downloader.http_client import HttpClient
from hls_caselaw_downloader.manifest import ManifestEntry, ManifestStore
from hls_caselaw_downloader.planner import RemoteFile

logger = logging.getLogger("hls_caselaw_downloader.downloader")

HEARTBEAT_EVERY_FILES = 25
HEARTBEAT_EVERY_SECONDS = 10.0


@dataclass
class DownloadResult:
    url: str
    local_path: Path
    status: str  # downloaded | skipped_not_modified | failed
    size_bytes: int = 0
    error: str | None = None


@dataclass
class SyncSummary:
    planned: int = 0
    downloaded: int = 0
    skipped_not_modified: int = 0
    failed: int = 0
    total_bytes: int = 0
    duration_seconds: float = 0.0
    results: list[DownloadResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_write(local_path: Path, content: bytes) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = local_path.with_name(local_path.name + ".part")
    tmp_path.write_bytes(content)
    os.replace(tmp_path, local_path)


def _fetch_expected_tar_sha256(client: HttpClient, tar_url: str) -> str | None:
    sha_url = tar_url + ".sha256"
    result = client.conditional_get(sha_url)
    if result.status != "ok" or result.content is None:
        logger.warning("Could not fetch %s to verify tar checksum: %s", sha_url, result.error)
        return None
    try:
        return checksum.parse_sha256sum_file(result.content)
    except ValueError as e:
        logger.warning("Could not parse %s: %s", sha_url, e)
        return None


def download_one(
    client: HttpClient, manifest: ManifestStore, remote_file: RemoteFile, dry_run: bool = False
) -> DownloadResult:
    """Fetch one file, verify it, and record it in the manifest.

    In `dry_run` mode the manifest is never written and no bytes are ever
    written to disk — but a conditional GET still happens (cheap for
    already-current files: a 304 has no body), so the result accurately
    reflects what a real `sync` would do.
    """
    prior = manifest.get(remote_file.url)
    file_exists = remote_file.local_path.exists()

    etag = prior.etag if (prior and file_exists) else None
    last_modified = prior.last_modified if (prior and file_exists) else None

    logger.debug("fetching %s", remote_file.url)
    result = client.conditional_get(remote_file.url, etag=etag, last_modified=last_modified)
    now = _now_iso()

    if result.status == "not_modified":
        if not dry_run:
            assert prior is not None
            prior.last_checked_at = now
            manifest.upsert(prior)
        logger.debug("not modified: %s", remote_file.url)
        return DownloadResult(remote_file.url, remote_file.local_path, "skipped_not_modified")

    if result.status == "error" or result.content is None:
        if not dry_run:
            entry = prior or ManifestEntry(
                url=remote_file.url, local_path=str(remote_file.local_path), format=remote_file.format
            )
            entry.status = "http_error"
            entry.last_checked_at = now
            manifest.upsert(entry)
        logger.warning("failed to fetch %s: %s", remote_file.url, result.error)
        return DownloadResult(remote_file.url, remote_file.local_path, "failed", error=result.error)

    content = result.content
    sha256_hex: str | None = None
    if remote_file.format == "tar":
        expected = _fetch_expected_tar_sha256(client, remote_file.url)
        if expected is not None:
            try:
                checksum.verify_sha256(content, expected)
                sha256_hex = expected
            except checksum.ChecksumMismatch as e:
                if not dry_run:
                    entry = ManifestEntry(
                        url=remote_file.url,
                        local_path=str(remote_file.local_path),
                        format=remote_file.format,
                        status="checksum_failed",
                        last_checked_at=now,
                    )
                    manifest.upsert(entry)
                logger.error("%s", e)
                return DownloadResult(remote_file.url, remote_file.local_path, "failed", error=str(e))

    if dry_run:
        logger.debug("would download %s (%d bytes)", remote_file.url, len(content))
        return DownloadResult(remote_file.url, remote_file.local_path, "downloaded", size_bytes=len(content))

    _atomic_write(remote_file.local_path, content)

    entry = ManifestEntry(
        url=remote_file.url,
        local_path=str(remote_file.local_path),
        format=remote_file.format,
        etag=result.etag,
        last_modified=result.last_modified,
        sha256=sha256_hex,
        git_blob_sha1=checksum.git_blob_sha1(content),
        size_bytes=len(content),
        status="ok",
        first_fetched_at=(prior.first_fetched_at if prior else now),
        last_checked_at=now,
        last_changed_at=now,
    )
    manifest.upsert(entry)
    logger.debug("downloaded %s (%d bytes)", remote_file.url, len(content))
    return DownloadResult(remote_file.url, remote_file.local_path, "downloaded", size_bytes=len(content))


@dataclass
class VerifyResult:
    checked: int = 0
    ok: int = 0
    mismatched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def verify_local_files(manifest_entries: list[ManifestEntry]) -> VerifyResult:
    """Re-checksum already-downloaded files against what the manifest recorded,
    without any network access — catches local corruption/tampering, not
    remote staleness (that's what `sync` itself detects via conditional GET)."""
    result = VerifyResult()
    for entry in manifest_entries:
        if entry.status != "ok":
            continue
        result.checked += 1
        path = Path(entry.local_path)
        if not path.exists():
            result.missing.append(entry.url)
            continue
        data = path.read_bytes()
        if entry.sha256:
            ok = checksum.sha256_hex(data).lower() == entry.sha256.lower()
        elif entry.git_blob_sha1:
            ok = checksum.git_blob_sha1(data) == entry.git_blob_sha1
        else:
            ok = True  # nothing to compare against
        if ok:
            result.ok += 1
        else:
            result.mismatched.append(entry.url)
    return result


def sync_files(
    client: HttpClient,
    manifest: ManifestStore,
    files: list[RemoteFile],
    max_workers: int = 4,
    limit: int | None = None,
    dry_run: bool = False,
) -> SyncSummary:
    if limit is not None:
        files = files[:limit]

    summary = SyncSummary(planned=len(files))
    start = time.monotonic()
    last_heartbeat_at = start
    completed = 0

    logger.info("starting %s: %d files planned", "dry-run" if dry_run else "sync", len(files))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(download_one, client, manifest, f, dry_run): f for f in files}
        for future in as_completed(futures):
            remote_file = futures[future]
            try:
                result = future.result()
            except Exception as e:
                logger.exception("unexpected error downloading %s", remote_file.url)
                result = DownloadResult(remote_file.url, remote_file.local_path, "failed", error=str(e))

            summary.results.append(result)
            completed += 1
            if result.status == "downloaded":
                summary.downloaded += 1
                summary.total_bytes += result.size_bytes
            elif result.status == "skipped_not_modified":
                summary.skipped_not_modified += 1
            else:
                summary.failed += 1

            now = time.monotonic()
            if completed % HEARTBEAT_EVERY_FILES == 0 or (now - last_heartbeat_at) >= HEARTBEAT_EVERY_SECONDS:
                elapsed = now - start
                logger.info(
                    "progress: %d/%d done (downloaded=%d skipped=%d failed=%d) — elapsed %.0fs",
                    completed,
                    summary.planned,
                    summary.downloaded,
                    summary.skipped_not_modified,
                    summary.failed,
                    elapsed,
                )
                last_heartbeat_at = now

    summary.duration_seconds = time.monotonic() - start
    logger.info(
        "sync finished: %d/%d downloaded, %d skipped, %d failed, %d bytes, %.1fs",
        summary.downloaded,
        summary.planned,
        summary.skipped_not_modified,
        summary.failed,
        summary.total_bytes,
        summary.duration_seconds,
    )
    return summary
